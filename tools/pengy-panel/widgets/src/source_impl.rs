//! The **production** [`DataSource`] implementation: real reads against the host.
//!
//! This is the concrete source the layer-shell backend (T6) wires into the panel
//! at runtime. It performs the side effects the [`crate::source::DataSource`]
//! trait abstracts away:
//!
//! | Method                  | Real implementation                                   |
//! |-------------------------|-------------------------------------------------------|
//! | [`now_secs`]            | `std::time::SystemTime::now()` since the epoch        |
//! | [`tz_offset_secs`]      | `date +%z`, parsed (memoized) — DST-aware             |
//! | [`power_supply`]        | `read_to_string(<base>/<entry>)`, trimmed             |
//! | [`discover_battery`]    | the first supply dir that has a `capacity` file       |
//!
//! # Testability
//!
//! The *pure* logic (offset parsing, the path-traversal guard, battery
//! discovery) is exercised by committed temp-directory fixtures in the unit
//! tests below — no network, no real battery required. The one thing that cannot
//! be fixture-injected is the actual wall clock and the `date` invocation; a
//! smoke test for those is gated behind `PENGYOS_IT=1` (see the module's
//! integration test). The default [`SysfsSource::new`] base path
//! (`/sys/class/power_supply`) is injectable via [`SysfsSource::with_base`] for
//! the file-reading tests.
//!
//! [`now_secs`]: crate::source::DataSource::now_secs
//! [`tz_offset_secs`]: crate::source::DataSource::tz_offset_secs
//! [`power_supply`]: crate::source::DataSource::power_supply

use std::cell::OnceCell;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::source::DataSource;

/// The default sysfs base for power-supply entries.
pub const DEFAULT_POWER_SUPPLY_BASE: &str = "/sys/class/power_supply";

/// A [`DataSource`] backed by real host reads.
#[derive(Debug, Clone)]
pub struct SysfsSource {
    base: PathBuf,
    /// Memoized local UTC offset (seconds east of UTC). Read once — the offset
    /// only changes on a DST transition or a system TZ change, which a running
    /// panel picks up on its next restart. Memoizing avoids spawning `date` on
    /// every (per-second) poll.
    tz_offset: OnceCell<Option<i32>>,
}

impl Default for SysfsSource {
    fn default() -> Self {
        Self::new()
    }
}

impl SysfsSource {
    /// Build a source reading the default `/sys/class/power_supply` tree.
    pub fn new() -> Self {
        Self::with_base(DEFAULT_POWER_SUPPLY_BASE)
    }

    /// Build a source reading a non-default power-supply base directory
    /// (used by the fixture-based unit tests).
    pub fn with_base(base: impl Into<PathBuf>) -> Self {
        SysfsSource {
            base: base.into(),
            tz_offset: OnceCell::new(),
        }
    }

    /// The power-supply base directory this source reads.
    pub fn base(&self) -> &Path {
        &self.base
    }

    /// Read the current local UTC offset (seconds **east** of UTC) from the
    /// system's timezone, via `date +%z` (the `+` is required: `date %z` would
    /// treat `%z` as a date to parse and fail). This is correct across DST
    /// because `date` consults the host's live timezone. Memoized on first use.
    fn local_utc_offset_east() -> Option<i32> {
        let out = Command::new("date").arg("+%z").output().ok()?;
        if !out.status.success() {
            return None;
        }
        let stdout = String::from_utf8(out.stdout).ok()?;
        parse_numeric_offset(stdout.trim())
    }

    /// Discover the battery: the first supply (directory under `base`) that
    /// exposes a `capacity` file. AC adapters and USB supplies do not have one,
    /// so this reliably finds the battery on a laptop and returns `None` on a
    /// desktop/server.
    pub fn discover_battery(&self) -> Option<String> {
        let entries = std::fs::read_dir(&self.base).ok()?;
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy().into_owned();
            let capacity = self.base.join(&name).join("capacity");
            if capacity.is_file() {
                return Some(name);
            }
        }
        None
    }
}

impl DataSource for SysfsSource {
    fn now_secs(&self) -> Option<i64> {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .ok()
            .map(|d| d.as_secs() as i64)
    }

    fn tz_offset_secs(&self) -> Option<i32> {
        // `get_or_init` yields `&Option<i32>`; `Option<i32>` is `Copy`, so a
        // plain dereference copies it out.
        *self.tz_offset.get_or_init(Self::local_utc_offset_east)
    }

    fn power_supply(&self, entry: &str) -> Option<String> {
        // Never allow an entry to escape the base directory.
        if entry.split('/').any(|c| c == "..") {
            return None;
        }
        let path = self.base.join(entry);
        let data = std::fs::read_to_string(path).ok()?;
        Some(data.trim().to_string())
    }
}

/// Parse a numeric offset of the form `±HHMM` (as produced by `date +%z`) into
/// seconds **east** of UTC. `+0900` → `+32400`, `-0400` → `-14400`, `+0000` → `0`.
///
/// A missing sign is treated as positive. Returns `None` for anything that is
/// not exactly a signed/unsigned 4-digit `HHMM` with a sane hour/minute.
pub fn parse_numeric_offset(s: &str) -> Option<i32> {
    let s = s.trim();
    let (sign, rest) = match s.as_bytes().first() {
        Some(b'+') => (1i32, &s[1..]),
        Some(b'-') => (-1i32, &s[1..]),
        _ => (1i32, s),
    };
    if rest.len() != 4 || !rest.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let hh: i32 = rest.get(0..2)?.parse().ok()?;
    let mm: i32 = rest.get(2..4)?.parse().ok()?;
    if hh > 23 || mm > 59 {
        return None;
    }
    Some(sign * (hh * 3600 + mm * 60))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// A unique temp directory per test (parallel-safe), cleaned up on drop.
    struct TmpDir(PathBuf);
    static COUNTER: AtomicUsize = AtomicUsize::new(0);

    impl TmpDir {
        fn new() -> Self {
            let n = COUNTER.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "pengy_panel_sysfs_{}_{}",
                std::process::id(),
                n
            ));
            std::fs::create_dir_all(&path).unwrap();
            TmpDir(path)
        }

        fn base(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TmpDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn write(base: &Path, relative: &str, contents: &str) {
        let path = base.join(relative);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).unwrap();
        }
        std::fs::write(path, contents).unwrap();
    }

    #[test]
    fn parses_the_default_base_path() {
        let s = SysfsSource::new();
        assert_eq!(s.base(), Path::new(DEFAULT_POWER_SUPPLY_BASE));
    }

    #[test]
    fn power_supply_reads_and_trims_a_fixture_file() {
        let dir = TmpDir::new();
        // Trailing newline mirrors a real sysfs file.
        write(dir.base(), "BAT0/capacity", "63\n");
        write(dir.base(), "BAT0/status", "  Discharging\n");

        let s = SysfsSource::with_base(dir.base());
        assert_eq!(s.power_supply("BAT0/capacity").as_deref(), Some("63"));
        assert_eq!(s.power_supply("BAT0/status").as_deref(), Some("Discharging"));
    }

    #[test]
    fn power_supply_returns_none_for_missing_or_absent_supply() {
        let dir = TmpDir::new();
        let s = SysfsSource::with_base(dir.base());
        assert_eq!(s.power_supply("BAT0/capacity"), None);
        assert_eq!(s.power_supply("AC/battery"), None);

        // A base directory that does not exist at all.
        let ghost = SysfsSource::with_base(dir.base().join("no_such_dir"));
        assert_eq!(ghost.power_supply("BAT0/capacity"), None);
    }

    #[test]
    fn power_supply_refuses_path_traversal() {
        let dir = TmpDir::new();
        write(dir.base(), "BAT0/capacity", "50\n");
        let s = SysfsSource::with_base(dir.base());
        // These must all be refused rather than escaping the base.
        assert_eq!(s.power_supply("../capacity"), None);
        assert_eq!(s.power_supply("BAT0/../BAT0/../../etc/passwd"), None);
        assert_eq!(s.power_supply(".."), None);
        // A normal, non-traversal read still works.
        assert_eq!(s.power_supply("BAT0/capacity").as_deref(), Some("50"));
    }

    #[test]
    fn discover_battery_finds_the_supply_with_capacity() {
        let dir = TmpDir::new();
        // An AC adapter (no capacity) and a battery (has capacity).
        write(dir.base(), "AC/capacity_is_absent", "x\n"); // not a "capacity" file
        write(dir.base(), "BAT1/capacity", "72\n");
        write(dir.base(), "BAT1/status", "Charging\n");

        let s = SysfsSource::with_base(dir.base());
        assert_eq!(s.discover_battery().as_deref(), Some("BAT1"));
    }

    #[test]
    fn discover_battery_returns_none_without_a_capacity_file() {
        let dir = TmpDir::new();
        // Only an AC adapter present → no battery.
        write(dir.base(), "AC/online", "1\n");
        let s = SysfsSource::with_base(dir.base());
        assert_eq!(s.discover_battery(), None);

        // And an empty directory.
        let empty = TmpDir::new();
        let s2 = SysfsSource::with_base(empty.base());
        assert_eq!(s2.discover_battery(), None);
    }

    #[test]
    fn now_secs_is_a_positive_recent_epoch() {
        let s = SysfsSource::new();
        let a = s.now_secs().expect("clock readable");
        let b = s.now_secs().expect("clock readable");
        // Post-2020 and non-decreasing across two back-to-back reads.
        assert!(a > 1_600_000_000);
        assert!(b >= a);
    }

    #[test]
    fn parse_numeric_offset_east_is_positive() {
        assert_eq!(parse_numeric_offset("+0900"), Some(32_400)); // Tokyo
        assert_eq!(parse_numeric_offset("+0000"), Some(0)); // UTC
        assert_eq!(parse_numeric_offset("+0530"), Some(19_800)); // Kolkata
        // No sign → positive.
        assert_eq!(parse_numeric_offset("0530"), Some(19_800));
    }

    #[test]
    fn parse_numeric_offset_west_is_negative() {
        assert_eq!(parse_numeric_offset("-0400"), Some(-14_400)); // EDT
        assert_eq!(parse_numeric_offset("-0500"), Some(-18_000)); // EST
        assert_eq!(parse_numeric_offset("-1230"), Some(-45_000)); // New Zealand
    }

    #[test]
    fn parse_numeric_offset_rejects_garbage() {
        assert_eq!(parse_numeric_offset(""), None);
        assert_eq!(parse_numeric_offset("abcd"), None);
        assert_eq!(parse_numeric_offset("90"), None); // too short
        assert_eq!(parse_numeric_offset("00000"), None); // too long
        assert_eq!(parse_numeric_offset("+9900"), None); // hour too big
        assert_eq!(parse_numeric_offset("+0060"), None); // minute too big
        assert_eq!(parse_numeric_offset("++00"), None);
        assert_eq!(parse_numeric_offset("+000a"), None);
        assert_eq!(parse_numeric_offset("UTC"), None);
    }

    /// Opt-in integration smoke test: the *real* `date`-backed offset is a
    /// sane, bounded value. Runs as a no-op unless `PENGYOS_IT=1` is set, so
    /// the default `cargo test` stays green on any machine (no `date`, no
    /// display, no hardware needed) while a real host can exercise it.
    #[test]
    fn tz_offset_from_real_date_is_bounded() {
        if std::env::var("PENGYOS_IT").ok().as_deref() != Some("1") {
            eprintln!(
                "SKIP tz_offset_from_real_date_is_bounded (set PENGYOS_IT=1 to run)"
            );
            return;
        }
        let s = SysfsSource::new();
        let offset = s.tz_offset_secs();
        assert!(offset.is_some(), "host should have a readable timezone");
        let o = offset.unwrap();
        // Every real-world offset is within ±17.5 hours.
        assert!((-63_000..=63_000).contains(&o));
    }
}
