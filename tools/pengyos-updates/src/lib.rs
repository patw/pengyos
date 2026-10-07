//! PengyOS Updates — what the app and the notifier decide, without a display.
//!
//! Inputs, all written by root-side `pengyos-update`:
//! * `/var/lib/pengyos-update/state.json` — the daily check's answer
//!   ([`SystemState`]);
//! * `/run/pengyos-update/progress.json` — a running install ([`Progress`]);
//! * `/etc/os-release` — what is running now.
//!
//! The user's own choices live in `~/.config/pengyos-updates/settings.json`
//! ([`Prefs`]): whether to be told at all, and "remind me later" for the update
//! on offer. The machine checks daily either way; these only decide whether a
//! notification appears.

#[cfg(feature = "cosmic")]
pub mod app;
#[cfg(feature = "cosmic")]
pub mod notifier;

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

pub const STATE: &str = "/var/lib/pengyos-update/state.json";
pub const PROGRESS: &str = "/run/pengyos-update/progress.json";
pub const OS_RELEASE: &str = "/etc/os-release";
pub const APP_ID: &str = "io.catbee.pengyos.Updates";

pub const DAY: i64 = 86_400;
/// A notification about the same thing is repeated at most this often.
pub const RENOTIFY: i64 = DAY;

pub fn now() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Versions and times
// ---------------------------------------------------------------------------

/// `YY.MM.N` (dotted integers) -> comparable vector; junk sorts lowest.
pub fn version_key(v: &str) -> Vec<u64> {
    v.trim().split('.').map(|p| p.parse().unwrap_or(0)).collect()
}

pub fn newer(a: &str, b: &str) -> bool {
    version_key(a) > version_key(b)
}

/// `2026-10-01T00:57:27Z` -> unix seconds (UTC; the only form we write).
pub fn parse_iso(s: &str) -> Option<i64> {
    let s = s.trim().trim_end_matches('Z');
    let (date, time) = s.split_once('T')?;
    let mut d = date.split('-').map(|x| x.parse::<i64>());
    let (y, m, day) = (d.next()?.ok()?, d.next()?.ok()?, d.next()?.ok()?);
    let mut t = time.split(':').map(|x| x.parse::<i64>());
    let (hh, mm, ss) = (t.next()?.ok()?, t.next()?.ok()?, t.next().unwrap_or(Ok(0)).ok()?);
    // Days from civil (Howard Hinnant's algorithm).
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * DAY + hh * 3600 + mm * 60 + ss)
}

/// "just now", "12 minutes ago", "3 hours ago", "2 days ago".
pub fn ago(then: i64, now: i64) -> String {
    let d = (now - then).max(0);
    let plural = |n: i64, unit: &str| format!("{n} {unit}{} ago", if n == 1 { "" } else { "s" });
    match d {
        0..=59 => "just now".into(),
        60..=3599 => plural(d / 60, "minute"),
        3600..=86_399 => plural(d / 3600, "hour"),
        _ => plural(d / DAY, "day"),
    }
}

/// A future time in words: "tomorrow", "in 3 days", "in 4 weeks".
pub fn until(then: i64, now: i64) -> String {
    let days = ((then - now).max(0) + DAY - 1) / DAY;
    match days {
        0 => "later today".into(),
        1 => "tomorrow".into(),
        2..=13 => format!("in {days} days"),
        14..=27 => format!("in {} weeks", (days + 3) / 7),
        28..=45 => "in a month".into(),
        _ => format!("in {} months", (days + 15) / 30),
    }
}

// ---------------------------------------------------------------------------
// What root says
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Available {
    pub version: String,
    pub title: String,
    pub notes: String,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct SystemState {
    pub name: String,
    pub image: String,
    pub current: String,
    pub channel: String,
    pub checked: Option<i64>,
    pub error: Option<String>,
    pub available: Option<Available>,
    /// Installed in the other slot, waiting for a restart.
    pub staged: Option<String>,
    /// What the other slot holds (the version a rollback goes to).
    pub other: Option<String>,
}

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

fn opt(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).filter(|x| !x.is_empty()).map(str::to_string)
}

impl SystemState {
    pub fn parse(text: &str) -> SystemState {
        let Ok(v) = serde_json::from_str::<Value>(text) else { return SystemState::default() };
        SystemState {
            name: s(&v, "name"),
            image: s(&v, "image"),
            current: s(&v, "current"),
            channel: s(&v, "channel"),
            checked: v.get("checked").and_then(Value::as_str).and_then(parse_iso),
            error: opt(&v, "error"),
            available: v.get("available").filter(|a| a.is_object()).map(|a| Available {
                version: s(a, "version"),
                title: s(a, "title"),
                notes: s(a, "notes"),
            }),
            staged: opt(&v, "staged"),
            other: opt(&v, "other"),
        }
    }

    pub fn load(path: &Path) -> SystemState {
        fs::read_to_string(path).map(|t| SystemState::parse(&t)).unwrap_or_default()
    }

    /// The update on offer for a machine running `running` (None if nothing
    /// newer, or it is already installed and waiting for a restart).
    pub fn offer(&self, running: &str) -> Option<&Available> {
        self.available.as_ref().filter(|a| {
            !a.version.is_empty() && newer(&a.version, running) && self.staged.as_deref() != Some(&a.version)
        })
    }

    /// A newer version installed in the other slot, waiting for a restart.
    pub fn pending_restart(&self, running: &str) -> Option<&str> {
        self.staged.as_deref().filter(|v| *v != running)
    }
}

/// `/etc/os-release` fields we show.
#[derive(Clone, Debug, Default)]
pub struct Running {
    pub name: String,
    pub version: String,
    pub image: String,
}

impl Running {
    pub fn parse(text: &str) -> Running {
        let mut r = Running::default();
        let mut variant = String::new();
        for line in text.lines() {
            let Some((k, v)) = line.split_once('=') else { continue };
            let v = v.trim().trim_matches('"').to_string();
            match k.trim() {
                "NAME" => r.name = v,
                "VARIANT" => variant = v,
                "IMAGE_VERSION" => r.version = v,
                "IMAGE_ID" => r.image = v,
                _ => {}
            }
        }
        if !variant.is_empty() {
            r.name = format!("{} {variant}", if r.name.is_empty() { "PengyOS" } else { &r.name });
        }
        r
    }

    pub fn load() -> Running {
        Running::parse(&fs::read_to_string(OS_RELEASE).unwrap_or_default())
    }
}

/// A running install, from `progress.json`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Progress {
    pub phase: String,
    pub fraction: f32,
    pub message: String,
    pub version: Option<String>,
}

impl Progress {
    pub fn parse(text: &str) -> Option<Progress> {
        let v: Value = serde_json::from_str(text).ok()?;
        Some(Progress {
            phase: s(&v, "phase"),
            fraction: v.get("fraction").and_then(Value::as_f64).unwrap_or(0.0) as f32,
            message: s(&v, "message"),
            version: opt(&v, "version"),
        })
    }

    pub fn finished(&self) -> bool {
        self.phase == "done" || self.phase == "error"
    }
}

// ---------------------------------------------------------------------------
// What the user wants
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Prefs {
    /// Show notifications at all (the machine still checks daily).
    pub notify: bool,
    /// "Remind me later": quiet about `snoozed_version` until this time. A
    /// newer release than the snoozed one is announced right away.
    pub snooze_until: i64,
    pub snoozed_version: String,
    /// What was last announced, and when (one reminder a day at most).
    pub notified: String,
    pub notified_at: i64,
}

impl Default for Prefs {
    fn default() -> Self {
        Prefs { notify: true, snooze_until: 0, snoozed_version: String::new(), notified: String::new(), notified_at: 0 }
    }
}

/// The "Remind me later" choices.
pub const SNOOZES: [(&str, i64); 4] =
    [("Tomorrow", DAY), ("In 3 days", 3 * DAY), ("In a week", 7 * DAY), ("In a month", 30 * DAY)];

impl Prefs {
    pub fn path() -> PathBuf {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        base.join("pengyos-updates").join("settings.json")
    }

    pub fn parse(text: &str) -> Prefs {
        let Ok(v) = serde_json::from_str::<Value>(text) else { return Prefs::default() };
        let d = Prefs::default();
        Prefs {
            notify: v.get("notify").and_then(Value::as_bool).unwrap_or(d.notify),
            snooze_until: v.get("snooze_until").and_then(Value::as_i64).unwrap_or(0),
            snoozed_version: s(&v, "snoozed_version"),
            notified: s(&v, "notified"),
            notified_at: v.get("notified_at").and_then(Value::as_i64).unwrap_or(0),
        }
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(&json!({
            "notify": self.notify,
            "snooze_until": self.snooze_until,
            "snoozed_version": self.snoozed_version,
            "notified": self.notified,
            "notified_at": self.notified_at,
        }))
        .unwrap_or_default()
    }

    pub fn load_from(path: &Path) -> Prefs {
        fs::read_to_string(path).map(|t| Prefs::parse(&t)).unwrap_or_default()
    }

    pub fn save_to(&self, path: &Path) -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("json.new");
        fs::write(&tmp, self.to_json() + "\n")?;
        fs::rename(tmp, path)
    }

    pub fn load() -> Prefs {
        Prefs::load_from(&Prefs::path())
    }

    pub fn save(&self) -> std::io::Result<()> {
        self.save_to(&Prefs::path())
    }

    pub fn snooze(&mut self, version: &str, secs: i64, now: i64) {
        self.snooze_until = now + secs;
        self.snoozed_version = version.to_string();
    }

    pub fn clear_snooze(&mut self) {
        self.snooze_until = 0;
        self.snoozed_version.clear();
    }

    /// Reminders for `version` are paused until (unix seconds), if they are.
    pub fn snoozed(&self, version: &str, now: i64) -> Option<i64> {
        (self.snoozed_version == version && now < self.snooze_until).then_some(self.snooze_until)
    }
}

/// What the notifier should say now, if anything.
#[derive(Clone, Debug, PartialEq)]
pub enum Notice {
    Available { version: String },
    Restart { version: String },
}

impl Notice {
    /// Key recorded in `Prefs::notified`, so each thing is announced once a day.
    pub fn key(&self) -> String {
        match self {
            Notice::Available { version } => format!("available:{version}"),
            Notice::Restart { version } => format!("restart:{version}"),
        }
    }

    pub fn text(&self, name: &str) -> (String, String) {
        match self {
            Notice::Available { version } => (
                format!("{name} {version} is available"),
                "Click to see what's new and update — or to be reminded later.".to_string(),
            ),
            Notice::Restart { version } => (
                format!("Restart to finish updating to {version}"),
                "The update is installed. It takes effect the next time this computer starts.".to_string(),
            ),
        }
    }
}

pub fn decide(state: &SystemState, running: &str, prefs: &Prefs, now: i64) -> Option<Notice> {
    if !prefs.notify {
        return None;
    }
    let notice = if let Some(v) = state.pending_restart(running) {
        Notice::Restart { version: v.to_string() }
    } else if let Some(a) = state.offer(running) {
        if prefs.snoozed(&a.version, now).is_some() {
            return None;
        }
        Notice::Available { version: a.version.clone() }
    } else {
        return None;
    };
    if prefs.notified == notice.key() && now - prefs.notified_at < RENOTIFY {
        return None;
    }
    Some(notice)
}

// ---------------------------------------------------------------------------
// Root actions: systemd units the app may start (polkit: wheel, no password)
// ---------------------------------------------------------------------------

pub const UNIT_CHECK: &str = "pengyos-update-check.service";
pub const UNIT_APPLY: &str = "pengyos-update-apply.service";
pub const UNIT_ROLLBACK: &str = "pengyos-update-rollback.service";

pub fn channel_unit(channel: &str) -> String {
    format!("pengyos-update-channel@{channel}.service")
}

/// `systemctl start UNIT` (waits for a oneshot to finish); Err = why not.
pub fn start_unit(unit: &str) -> Result<(), String> {
    let out = Command::new("systemctl")
        .args(["start", "--no-ask-password", unit])
        .output()
        .map_err(|e| format!("cannot run systemctl: {e}"))?;
    if out.status.success() {
        return Ok(());
    }
    let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
    if err.contains("nteractive authentication") || err.contains("Access denied") {
        return Err("Only an administrator of this computer can do that.".into());
    }
    Err(if err.is_empty() { format!("{unit} failed") } else { err })
}

#[cfg(test)]
mod tests {
    use super::*;

    const STATE_JSON: &str = r#"{
      "available": {"notes": "Faster.\n\nFixes.", "title": "PengyOS Desktop 26.09.3", "version": "26.09.3"},
      "channel": "testing", "checked": "2026-10-01T00:57:27Z", "current": "26.09.2",
      "error": null, "image": "pengyos-desktop", "name": "PengyOS Desktop",
      "other": "26.09.1", "staged": null
    }"#;

    #[test]
    fn iso_and_words() {
        assert_eq!(parse_iso("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_iso("2026-10-01T00:57:27Z"), Some(1_790_816_247));
        assert_eq!(parse_iso("garbage"), None);
        assert_eq!(ago(100, 130), "just now");
        assert_eq!(ago(0, 3 * 60), "3 minutes ago");
        assert_eq!(ago(0, 3600), "1 hour ago");
        assert_eq!(ago(0, 2 * DAY + 5), "2 days ago");
        assert_eq!(until(DAY, 0), "tomorrow");
        assert_eq!(until(3 * DAY, 0), "in 3 days");
        assert_eq!(until(30 * DAY, 0), "in a month");
        assert_eq!(until(14 * DAY, 0), "in 2 weeks");
    }

    #[test]
    fn versions() {
        assert!(newer("26.09.10", "26.09.9"));
        assert!(newer("26.10.0", "26.09.9"));
        assert!(!newer("26.09.2", "26.09.2"));
    }

    #[test]
    fn state_parse() {
        let st = SystemState::parse(STATE_JSON);
        assert_eq!(st.name, "PengyOS Desktop");
        assert_eq!(st.available.as_ref().unwrap().version, "26.09.3");
        assert_eq!(st.checked, Some(1_790_816_247));
        assert_eq!(st.other.as_deref(), Some("26.09.1"));
        assert_eq!(st.staged, None);
        assert!(st.offer("26.09.2").is_some());
        assert!(st.offer("26.09.3").is_none(), "running it already");
        assert_eq!(SystemState::parse("not json"), SystemState::default());
    }

    #[test]
    fn staged_is_not_offered_again() {
        let mut st = SystemState::parse(STATE_JSON);
        st.staged = Some("26.09.3".into());
        assert!(st.offer("26.09.2").is_none());
        assert_eq!(st.pending_restart("26.09.2"), Some("26.09.3"));
        assert_eq!(st.pending_restart("26.09.3"), None, "already restarted into it");
    }

    #[test]
    fn running_from_os_release() {
        let r = Running::parse("NAME=\"PengyOS\"\nVARIANT=\"Desktop NVIDIA\"\nIMAGE_ID=pengyos-desktop-nvidia\nIMAGE_VERSION=\"26.09.2\"\n");
        assert_eq!((r.name.as_str(), r.version.as_str()), ("PengyOS Desktop NVIDIA", "26.09.2"));
    }

    #[test]
    fn progress_parse() {
        let p = Progress::parse(r#"{"phase":"download","fraction":0.3,"message":"Downloading"}"#).unwrap();
        assert_eq!(p.phase, "download");
        assert!(!p.finished());
        assert!(Progress::parse(r#"{"phase":"done","fraction":1.0,"version":"26.09.3"}"#).unwrap().finished());
    }

    #[test]
    fn notify_rules() {
        let st = SystemState::parse(STATE_JSON);
        let t = 1_800_000_000;
        let mut p = Prefs::default();
        let n = decide(&st, "26.09.2", &p, t).unwrap();
        assert_eq!(n, Notice::Available { version: "26.09.3".into() });

        // once a day at most
        p.notified = n.key();
        p.notified_at = t;
        assert_eq!(decide(&st, "26.09.2", &p, t + 3600), None);
        assert!(decide(&st, "26.09.2", &p, t + DAY + 1).is_some());

        // remind me later
        p.snooze("26.09.3", 7 * DAY, t);
        assert_eq!(decide(&st, "26.09.2", &p, t + 2 * DAY), None);
        assert!(decide(&st, "26.09.2", &p, t + 8 * DAY).is_some());
        // ... but a newer release than the snoozed one is news
        let mut newer_st = st.clone();
        newer_st.available.as_mut().unwrap().version = "26.09.4".into();
        assert!(decide(&newer_st, "26.09.2", &p, t + 2 * DAY).is_some());

        // notifications off: nothing, ever (the app still shows it)
        p.notify = false;
        assert_eq!(decide(&st, "26.09.2", &p, t + 100 * DAY), None);
    }

    #[test]
    fn restart_reminder() {
        let mut st = SystemState::parse(STATE_JSON);
        st.staged = Some("26.09.3".into());
        let n = decide(&st, "26.09.2", &Prefs::default(), 1).unwrap();
        assert_eq!(n, Notice::Restart { version: "26.09.3".into() });
        assert!(n.text("PengyOS Desktop").0.contains("Restart"));
    }

    #[test]
    fn prefs_roundtrip() {
        let dir = std::env::temp_dir().join(format!("pengyos-updates-test-{}", std::process::id()));
        let path = dir.join("settings.json");
        let mut p = Prefs::default();
        p.notify = false;
        p.snooze("26.09.3", DAY, 10);
        p.save_to(&path).unwrap();
        assert_eq!(Prefs::load_from(&path), p);
        assert_eq!(Prefs::load_from(&dir.join("missing.json")), Prefs::default());
        let _ = fs::remove_dir_all(dir);
        assert_eq!(Prefs::parse("{}"), Prefs::default());
    }

    #[test]
    fn units() {
        assert_eq!(channel_unit("testing"), "pengyos-update-channel@testing.service");
    }
}
