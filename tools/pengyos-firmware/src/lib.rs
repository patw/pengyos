//! PengyOS Firmware — what the app and the notifier decide, without a display.
//!
//! Inputs:
//! * `fwupdmgr get-updates --json` + `fwupdmgr get-devices --json` — what the
//!   daemon says ([`FirmwareState`]); root's daily check saves the answer to
//!   `/var/lib/pengyos-firmware/state.json`, which is what the app and the
//!   notifier read, so neither needs the network, the daemon's privileges, or a
//!   session of its own.
//! * `/run/pengyos-firmware/progress.json` — a running install ([`Progress`]),
//!   written by `pengyos-firmware apply` while it runs as root.
//!
//! The user's own choices live in `~/.config/pengyos-firmware/settings.json`
//! ([`Prefs`]): whether to be told at all, and "remind me later" for what is on
//! offer. The machine checks daily either way; these only decide whether a
//! notification appears — the same contract as `pengyos-updates`.
//!
//! Writing firmware is irreversible on hardware. Nothing here does it: installing
//! is a systemd unit the user starts (polkit lets administrators do it without a
//! password, see `60-pengyos-firmware.rules`), and the unit runs `fwupdmgr` with
//! the prompts answered and the reboot left to the person.

#[cfg(feature = "cosmic")]
pub mod app;
#[cfg(feature = "cosmic")]
pub mod notifier;

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

pub const STATE: &str = "/var/lib/pengyos-firmware/state.json";
pub const PROGRESS: &str = "/run/pengyos-firmware/progress.json";
pub const OS_RELEASE: &str = "/etc/os-release";
pub const APP_ID: &str = "io.catbee.pengyos.Firmware";

pub const DAY: i64 = 86_400;
/// A notification about the same thing is repeated at most this often.
pub const RENOTIFY: i64 = DAY;

pub fn now() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

// ---------------------------------------------------------------------------
// Times
// ---------------------------------------------------------------------------

/// `2026-10-15T18:04:00Z` -> unix seconds (UTC; the only form we write).
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

/// Unix seconds -> `2026-10-15T18:04:00Z` (the inverse of [`parse_iso`]).
pub fn iso(secs: i64) -> String {
    let days = secs.div_euclid(DAY);
    let rem = secs.rem_euclid(DAY);
    let (hh, mm, ss) = (rem / 3600, rem % 3600 / 60, rem % 60);
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{hh:02}:{mm:02}:{ss:02}Z")
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

/// "1.2 GB", "512 MB", "84 KB" for a firmware payload.
pub fn human_size(bytes: u64) -> String {
    const K: u64 = 1024;
    if bytes >= K * K * K {
        format!("{} GB", (bytes as f64 / (K * K * K) as f64 * 10.0).round() / 10.0)
    } else if bytes >= K * K {
        format!("{} MB", (bytes as f64 / (K * K) as f64).round())
    } else if bytes >= K {
        format!("{} KB", (bytes as f64 / K as f64).round())
    } else {
        format!("{bytes} bytes")
    }
}

// ---------------------------------------------------------------------------
// What fwupd says
// ---------------------------------------------------------------------------
//
// fwupd 2.x serialises a device with keys like `DeviceId`, `Name`, `Plugin`,
// `Version`, `Flags` (lowercase-hyphenated: `internal`, `updatable`,
// `needs-reboot`, `require-ac`, …) and `Updates`, one object per release with
// `Version` (the new version; `NewVersion` in fwupd 1.x), `Description`, `Size`,
// `Urgency`, `Categories` and `Flags`. Everything is read defensively: a key that
// is missing is empty, not a crash, and a device fwupd did not finish describing
// is skipped rather than shown as broken.

/// One firmware release fwupd offers for a device.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Release {
    pub version: String,
    pub description: String,
    pub size: u64,
    pub urgency: String,
    pub categories: Vec<String>,
    pub flags: Vec<String>,
}

impl Release {
    pub fn parse(v: &Value) -> Release {
        Release {
            // fwupd writes these keys capitalised; our own state file uses the
            // same names, and both are accepted.
            version: str(v, "Version").or_else(|| str(v, "NewVersion")).unwrap_or_default(),
            description: str(v, "Description").unwrap_or_default(),
            size: v.get("Size").and_then(Value::as_u64).unwrap_or(0),
            urgency: str(v, "Urgency").unwrap_or_default(),
            categories: strs(v, "Categories"),
            flags: strs(v, "Flags"),
        }
    }

    /// The release's own notes, as short paragraphs (fwupd sends AppStream
    /// markup; the tags are stripped, the paragraphs kept).
    pub fn notes(&self) -> Vec<String> {
        strip_markup(&self.description)
            .split("\n\n")
            .map(str::trim)
            .filter(|p| !p.is_empty())
            .map(str::to_string)
            .collect()
    }

    /// "critical" / "high" / "medium" / "low" as the user reads it.
    pub fn urgency_word(&self) -> Option<String> {
        match self.urgency.as_str() {
            "critical" => Some("Critical security update".into()),
            "high" => Some("Important security update".into()),
            "medium" => Some("Important update".into()),
            "low" => Some("Low priority".into()),
            _ => None,
        }
    }

    /// A security release (the AppStream category fwupd sets for one).
    pub fn is_security(&self) -> bool {
        self.categories.iter().any(|c| c == "security")
            || matches!(self.urgency.as_str(), "critical" | "high")
    }
}

/// One device, as fwupd describes it, with the update it offers.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Device {
    pub id: String,
    pub name: String,
    pub plugin: String,
    pub current: String,
    pub flags: Vec<String>,
    /// What fwupd offers (the newest release in `Updates`).
    pub available: Option<Release>,
    /// How the last attempt ended: `Success`, `Failed`, `Pending`, …
    pub update_state: String,
    pub update_message: String,
    pub update_error: String,
}

fn str(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

fn strs(v: &Value, k: &str) -> Vec<String> {
    v.get(k)
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).map(|s| s.trim().to_string()).collect())
        .unwrap_or_default()
}

/// AppStream markup (`<p>`, `<ul><li>`, …) -> plain text paragraphs.
fn strip_markup(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '<' {
            out.push(c);
            continue;
        }
        let mut tag = String::new();
        for c in chars.by_ref() {
            if c == '>' {
                break;
            }
            tag.push(c);
        }
        // A list item starts a line; a paragraph/list ends with a blank line.
        if tag.starts_with("li") {
            out.push_str("\n• ");
        } else if tag.starts_with("/p") || tag.starts_with("/ul") || tag.starts_with("/ol") {
            out.push('\n');
        }
    }
    out.replace("\r\n", "\n").trim().to_string()
}

/// Firmware versions are not one format (`0.1.20`, `2011`, `1E00`, `3.4.5.6`).
/// Dotted decimals compare exactly; anything else compares as strings, and
/// [`Device::offer`] treats a comparison it cannot make as "fwupd knows best".
pub fn version_key(v: &str) -> Vec<u64> {
    v.trim().split('.').map(|p| p.parse().unwrap_or(0)).collect()
}

pub fn newer(a: &str, b: &str) -> bool {
    version_key(a) > version_key(b)
}

/// Both sides are dotted decimals, so a comparison means something.
pub fn versions_comparable(a: &str, b: &str) -> bool {
    let clean = |s: &str| {
        !s.is_empty() && s.split('.').all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
    };
    clean(a) && clean(b)
}

impl Device {
    fn has_flag(&self, flag: &str) -> bool {
        self.flags.iter().any(|f| f == flag)
    }

    /// fwupd can update this device (and is not looking at a bootloader-mode
    /// shadow of it, which is not something the user can act on).
    pub fn updatable(&self) -> bool {
        (self.has_flag("updatable") || self.has_flag("updatable-hidden"))
            && !self.has_flag("is-bootloader")
            && !self.has_flag("historical")
    }

    pub fn requires_ac(&self) -> bool {
        self.has_flag("require-ac")
    }
    pub fn needs_shutdown(&self) -> bool {
        self.has_flag("needs-shutdown")
    }
    pub fn needs_reboot(&self) -> bool {
        self.has_flag("needs-reboot")
    }
    pub fn needs_activation(&self) -> bool {
        self.has_flag("needs-activation")
    }
    pub fn needs_bootloader(&self) -> bool {
        self.has_flag("needs-bootloader")
    }
    pub fn locked(&self) -> bool {
        self.has_flag("locked")
    }
    pub fn wait_for_replug(&self) -> bool {
        self.has_flag("wait-for-replug")
    }

    /// An update is staged in the hardware: it finishes at the next restart.
    pub fn pending_reboot(&self) -> bool {
        self.needs_reboot()
            && matches!(self.update_state.as_str(), "Success" | "Pending" | "Needs Reboot")
    }

    /// The update to offer: what fwupd found, for a device it can update, that
    /// is not already staged, and not older than what runs (when that can be
    /// judged at all — see [`versions_comparable`]).
    pub fn offer(&self) -> Option<&Release> {
        let a = self.available.as_ref()?;
        if !self.updatable() || a.version.is_empty() || self.pending_reboot() {
            return None;
        }
        if versions_comparable(&a.version, &self.current) && !newer(&a.version, &self.current) {
            return None;
        }
        Some(a)
    }

    /// What the user has to do for this device, in their words.
    pub fn warnings(&self) -> Vec<String> {
        let mut w = Vec::new();
        if self.requires_ac() {
            w.push("Plug this computer into the wall — it will not update on battery.".into());
        }
        if self.needs_shutdown() {
            w.push("This one applies while the computer is switched off: it will shut down and start again by itself.".into());
        } else if self.needs_reboot() {
            w.push("This one applies at the next restart.".into());
        }
        if self.needs_activation() {
            w.push("It takes effect after the hardware reactivates the update (usually at the next restart).".into());
        }
        if self.needs_bootloader() {
            w.push("The device has to restart into its update mode; the screen may go blank for a while.".into());
        }
        if self.wait_for_replug() {
            w.push("You may need to unplug the device and plug it back in.".into());
        }
        if self.locked() {
            w.push("This device is locked by its firmware and cannot be updated from here.".into());
        }
        w
    }
}

/// The whole answer of one check: the devices, and why it failed if it did.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct FirmwareState {
    pub checked: Option<i64>,
    pub error: Option<String>,
    pub fwupd: String,
    pub devices: Vec<Device>,
}

impl FirmwareState {
    /// Parse the JSON fwupdmgr prints (`{"Devices": […]}`), newest release first
    /// in each device's `Updates`.
    pub fn parse_fwupdmgr(text: &str) -> Vec<Device> {
        let Ok(v) = serde_json::from_str::<Value>(text) else { return Vec::new() };
        let list = v.get("Devices").and_then(Value::as_array);
        let Some(list) = list else { return Vec::new() };
        let mut out = Vec::new();
        for d in list {
            let id = str(d, "DeviceId").unwrap_or_default();
            let name = str(d, "Name").unwrap_or_else(|| id.clone());
            if id.is_empty() {
                continue; // nothing to act on without an id
            }
            let releases: Vec<Release> = d
                .get("Updates")
                .and_then(Value::as_array)
                .map(|a| a.iter().map(Release::parse).filter(|r| !r.version.is_empty()).collect())
                .unwrap_or_default();
            let available = releases.into_iter().max_by_key(|r| version_key(&r.version));
            out.push(Device {
                id,
                name,
                plugin: str(d, "Plugin").unwrap_or_default(),
                current: str(d, "Version").unwrap_or_default(),
                flags: strs(d, "Flags"),
                available,
                update_state: str(d, "UpdateState").unwrap_or_default(),
                update_message: str(d, "UpdateMessage").unwrap_or_default(),
                update_error: str(d, "UpdateError").unwrap_or_default(),
            });
        }
        out.sort_by(|a, b| a.name.cmp(&b.name));
        out
    }

    /// Merge the two JSON answers: `get-devices` lists every device, `get-updates`
    /// only the ones with something to install.
    pub fn merge(devices: &str, updates: &str) -> FirmwareState {
        let mut devices = Self::parse_fwupdmgr(devices);
        let offered = Self::parse_fwupdmgr(updates);
        for d in devices.iter_mut() {
            if let Some(o) = offered.iter().find(|o| o.id == d.id) {
                d.available = o.available.clone().filter(|a| {
                    !(versions_comparable(&a.version, &d.current) && !newer(&a.version, &d.current))
                });
            }
        }
        // A device only get-updates reported (a bootloader-mode twin, say) still counts.
        for o in offered {
            if !devices.iter().any(|d| d.id == o.id) {
                devices.push(o);
            }
        }
        devices.sort_by(|a, b| a.name.cmp(&b.name));
        FirmwareState { checked: Some(now()), devices, ..Default::default() }
    }

    /// Devices with something to install.
    pub fn offers(&self) -> Vec<&Device> {
        self.devices.iter().filter(|d| d.offer().is_some()).collect()
    }

    /// Devices whose update is staged and finishes at the next restart.
    pub fn pending_reboot(&self) -> Vec<&Device> {
        self.devices.iter().filter(|d| d.pending_reboot()).collect()
    }

    /// Devices fwupd can update, whatever they are running now.
    pub fn updatable(&self) -> Vec<&Device> {
        self.devices.iter().filter(|d| d.updatable()).collect()
    }

    /// "System Firmware and NVMe SSD", "System Firmware, NVMe SSD and 2 more".
    pub fn names(devices: &[&Device]) -> String {
        let names: Vec<&str> = devices.iter().map(|d| d.name.as_str()).collect();
        match names.as_slice() {
            [] => String::new(),
            [one] => one.to_string(),
            [a, b] => format!("{a} and {b}"),
            [a, b, c] => format!("{a}, {b} and {c}"),
            [a, b, rest @ ..] => format!("{a}, {b} and {} more", rest.len()),
        }
    }

    /// What the notifier records as announced: the devices and versions, so a
    /// new update is news and the same set is repeated at most once a day.
    pub fn key(&self) -> String {
        let mut parts: Vec<String> = self
            .offers()
            .iter()
            .map(|d| format!("{}:{}", d.id, d.offer().map(|a| a.version.as_str()).unwrap_or("")))
            .collect();
        parts.sort();
        parts.join(",")
    }

    pub fn to_json(&self) -> String {
        let devices: Vec<Value> = self
            .devices
            .iter()
            .map(|d| {
                json!({
                    "id": d.id,
                    "name": d.name,
                    "plugin": d.plugin,
                    "current": d.current,
                    "flags": d.flags,
                    // The same key names fwupd itself uses, so one parser reads
                    // both this file and fwupdmgr's output.
                    "available": d.available.as_ref().map(|a| json!({
                        "Version": a.version,
                        "Description": a.description,
                        "Size": a.size,
                        "Urgency": a.urgency,
                        "Categories": a.categories,
                        "Flags": a.flags,
                    })),
                    "update_state": d.update_state,
                    "update_message": d.update_message,
                    "update_error": d.update_error,
                })
            })
            .collect();
        serde_json::to_string_pretty(&json!({
            "checked": self.checked.map(iso),
            "error": self.error,
            "fwupd": self.fwupd,
            "devices": devices,
        }))
        .unwrap_or_default()
    }

    pub fn parse(text: &str) -> FirmwareState {
        let Ok(v) = serde_json::from_str::<Value>(text) else { return FirmwareState::default() };
        let devices = v
            .get("devices")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .map(|d| Device {
                        id: str(d, "id").unwrap_or_default(),
                        name: str(d, "name").unwrap_or_default(),
                        plugin: str(d, "plugin").unwrap_or_default(),
                        current: str(d, "current").unwrap_or_default(),
                        flags: strs(d, "flags"),
                        available: d.get("available").filter(|a| a.is_object()).map(Release::parse),
                        update_state: str(d, "update_state").unwrap_or_default(),
                        update_message: str(d, "update_message").unwrap_or_default(),
                        update_error: str(d, "update_error").unwrap_or_default(),
                    })
                    .collect()
            })
            .unwrap_or_default();
        FirmwareState {
            checked: v.get("checked").and_then(Value::as_str).and_then(parse_iso),
            error: v.get("error").and_then(Value::as_str).map(str::to_string),
            fwupd: str(&v, "fwupd").unwrap_or_default(),
            devices,
        }
    }

    pub fn load(path: &Path) -> FirmwareState {
        fs::read_to_string(path).map(|t| FirmwareState::parse(&t)).unwrap_or_default()
    }

    pub fn save_to(&self, path: &Path) -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("json.new");
        fs::write(&tmp, self.to_json() + "\n")?;
        fs::rename(tmp, path)
    }
}

// ---------------------------------------------------------------------------
// Running fwupdmgr
// ---------------------------------------------------------------------------

/// Ask the daemon. Err = what to tell the user (the daemon is not running, the
/// tool is not installed, the network is down).
pub fn fwupdmgr(args: &[&str]) -> Result<String, String> {
    let out = Command::new("fwupdmgr")
        .args(args)
        .output()
        .map_err(|e| format!("Cannot run fwupdmgr: {e}"))?;
    if out.status.success() {
        return Ok(String::from_utf8_lossy(&out.stdout).to_string());
    }
    let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
    let line = err.lines().next().unwrap_or("").trim().to_string();
    Err(if line.is_empty() { "fwupdmgr failed".into() } else { line })
}

/// The daemon's version, for the state file ("2.1.1").
pub fn fwupd_version() -> String {
    Command::new("fwupdmgr")
        .arg("--version")
        .output()
        .ok()
        .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
        .and_then(|t| {
            t.lines()
                .find(|l| l.contains("org.freedesktop.fwupd"))
                .and_then(|l| l.split_whitespace().last())
                .map(str::to_string)
        })
        .unwrap_or_default()
}

/// Ask for updates and merge the answer into a state. Takes a minute: it
/// refreshes the metadata from LVFS. This is root's daily job, not the app's.
pub fn check() -> Result<FirmwareState, String> {
    let updates = fwupdmgr(&["get-updates", "--json"])?;
    let devices = fwupdmgr(&["get-devices", "--json"]).unwrap_or_default();
    let mut state = FirmwareState::merge(&devices, &updates);
    state.fwupd = fwupd_version();
    Ok(state)
}

// ---------------------------------------------------------------------------
// Installing
// ---------------------------------------------------------------------------

/// A running install, from `progress.json`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Progress {
    pub phase: String,
    pub fraction: f32,
    pub message: String,
}

impl Progress {
    pub fn parse(text: &str) -> Option<Progress> {
        let v: Value = serde_json::from_str(text).ok()?;
        Some(Progress {
            phase: str(&v, "phase").unwrap_or_default(),
            fraction: v.get("fraction").and_then(Value::as_f64).unwrap_or(0.0) as f32,
            message: str(&v, "message").unwrap_or_default(),
        })
    }

    /// Fold one `fwupdmgr` output line into the progress: `Writing…: 45%` sets
    /// both phase and fraction, `Verifying…` sets the phase, a bare line is a
    /// message. fwupdmgr prints these with `\r` when on a terminal and as lines
    /// when it is not, so both are handled.
    pub fn from_line(&self, line: &str) -> Progress {
        let line = line.rsplit('\r').next().unwrap_or("").trim();
        if line.is_empty() {
            return self.clone();
        }
        let (text, fraction) = match line.rfind('%') {
            Some(i) => {
                let digits = line[..i]
                    .chars()
                    .rev()
                    .take_while(|c| c.is_ascii_digit())
                    .collect::<String>()
                    .chars()
                    .rev()
                    .collect::<String>();
                match digits.parse::<f32>() {
                    Ok(p) => (line[..i].trim_end_matches(digits.as_str()).trim(), Some(p / 100.0)),
                    Err(_) => (line, None),
                }
            }
            None => (line, None),
        };
        let text = text.trim_end_matches(':').trim();
        if text.is_empty() {
            return Progress { fraction: fraction.unwrap_or(self.fraction), ..self.clone() };
        }
        let word = text.split_whitespace().next().unwrap_or(text).to_string();
        Progress {
            phase: word,
            fraction: fraction.unwrap_or(self.fraction),
            message: text.to_string(),
        }
    }

    pub fn save_to(&self, path: &Path) -> std::io::Result<()> {
        if let Some(dir) = path.parent() {
            fs::create_dir_all(dir)?;
        }
        let tmp = path.with_extension("json.new");
        fs::write(
            &tmp,
            json!({"phase": self.phase, "fraction": self.fraction, "message": self.message})
                .to_string()
                + "\n",
        )?;
        fs::rename(tmp, path)
    }
}

/// Install. Run as root (`pengyos-firmware-update@<ids>.service`): the prompts
/// are answered, the metadata and history checks are skipped (root's check
/// already did them), and the reboot is left to the person — the unit never
/// restarts the computer. Progress is streamed to `progress.json` as it goes.
pub fn apply(ids: &[String], progress_path: &Path) -> Result<(), String> {
    let mut cmd = Command::new("fwupdmgr");
    cmd.args(["--assume-yes", "--no-unreported-check", "--no-remote-check", "--no-metadata-check"]);
    cmd.arg("update");
    cmd.args(ids);
    // stderr is never read here: inherit it (the unit sends it to the journal)
    // rather than pipe it, or a chatty fwupdmgr fills the pipe and stalls.
    let mut out = cmd.stdout(Stdio::piped()).stderr(Stdio::inherit()).spawn().map_err(|e| format!("Cannot run fwupdmgr: {e}"))?;
    let mut stdout = out.stdout.take().ok_or("fwupdmgr produced no output")?;
    let mut progress = Progress { phase: "Starting".into(), fraction: 0.0, message: "Starting…".into() };
    let _ = progress.save_to(progress_path);
    let mut tail = String::new();
    let mut buf = [0u8; 4096];
    loop {
        let n = std::io::Read::read(&mut stdout, &mut buf).unwrap_or(0);
        if n == 0 {
            break;
        }
        let chunk = String::from_utf8_lossy(&buf[..n]).to_string();
        tail.push_str(&chunk);
        let mut lines: Vec<&str> = tail.split(['\n', '\r']).map(str::trim).filter(|l| !l.is_empty()).collect();
        if let Some(last) = lines.pop() {
            progress = progress.from_line(last);
            let _ = progress.save_to(progress_path);
        }
        tail.clear();
    }
    if tail.trim().contains('%') {
        progress = progress.from_line(tail.trim());
        let _ = progress.save_to(progress_path);
    }
    let status = out.wait().map_err(|e| format!("fwupdmgr: {e}"))?;
    if status.success() {
        Progress { phase: "done".into(), fraction: 1.0, message: "Done".into() }.save_to(progress_path)
            .map_err(|e| e.to_string())?;
        return Ok(());
    }
    Progress { phase: "error".into(), fraction: progress.fraction, message: "The update did not finish".into() }
        .save_to(progress_path)
        .map_err(|e| e.to_string())?;
    Err("The firmware update did not finish. Nothing is changed until the hardware accepts it.".into())
}

// ---------------------------------------------------------------------------
// What the user wants
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, PartialEq)]
pub struct Prefs {
    /// Show notifications at all (the machine still checks daily).
    pub notify: bool,
    /// "Remind me later": quiet about `snoozed_key` until this time. A newer
    /// update than the snoozed set is announced right away.
    pub snooze_until: i64,
    pub snoozed_key: String,
    /// What was last announced, and when (one reminder a day at most).
    pub notified: String,
    pub notified_at: i64,
}

impl Default for Prefs {
    fn default() -> Self {
        Prefs { notify: true, snooze_until: 0, snoozed_key: String::new(), notified: String::new(), notified_at: 0 }
    }
}

/// The "Remind me later" choices — the same ones PengyOS Updates offers.
pub const SNOOZES: [(&str, i64); 4] =
    [("Tomorrow", DAY), ("In 3 days", 3 * DAY), ("In a week", 7 * DAY), ("In a month", 30 * DAY)];

impl Prefs {
    pub fn path() -> PathBuf {
        let base = std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        base.join("pengyos-firmware").join("settings.json")
    }

    pub fn parse(text: &str) -> Prefs {
        let Ok(v) = serde_json::from_str::<Value>(text) else { return Prefs::default() };
        Prefs {
            notify: v.get("notify").and_then(Value::as_bool).unwrap_or(true),
            snooze_until: v.get("snooze_until").and_then(Value::as_i64).unwrap_or(0),
            snoozed_key: str(&v, "snoozed_key").unwrap_or_default(),
            notified: str(&v, "notified").unwrap_or_default(),
            notified_at: v.get("notified_at").and_then(Value::as_i64).unwrap_or(0),
        }
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(&json!({
            "notify": self.notify,
            "snooze_until": self.snooze_until,
            "snoozed_key": self.snoozed_key,
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

    pub fn snooze(&mut self, key: &str, secs: i64, now: i64) {
        self.snooze_until = now + secs;
        self.snoozed_key = key.to_string();
    }

    pub fn clear_snooze(&mut self) {
        self.snooze_until = 0;
        self.snoozed_key.clear();
    }

    /// Reminders are paused until (unix seconds) if the snoozed set is what is
    /// on offer now; a newer set is news.
    pub fn snoozed(&self, key: &str, now: i64) -> Option<i64> {
        (self.snoozed_key == key && now < self.snooze_until).then_some(self.snooze_until)
    }
}

/// What the notifier should say now, if anything.
#[derive(Clone, Debug, PartialEq)]
pub enum Notice {
    Available { names: String, count: usize },
    Reboot { names: String },
}

impl Notice {
    /// Key recorded in `Prefs::notified`, so each thing is announced once a day.
    pub fn key(&self) -> String {
        match self {
            Notice::Available { names, count } => format!("available:{names}:{count}"),
            Notice::Reboot { names } => format!("reboot:{names}"),
        }
    }

    pub fn text(&self) -> (String, String) {
        match self {
            Notice::Available { names, count } => (
                match count {
                    1 => format!("Firmware update available: {names}"),
                    n => format!("{n} firmware updates are available"),
                },
                match count {
                    1 => format!("{names} has new firmware. Click to see it and install it."),
                    _ => format!("{}: click to see them and install them.", names),
                },
            ),
            Notice::Reboot { names } => (
                "Restart to finish a firmware update".into(),
                format!("{names} is waiting for a restart to take its new firmware."),
            ),
        }
    }
}

pub fn decide(state: &FirmwareState, prefs: &Prefs, now: i64) -> Option<Notice> {
    if !prefs.notify {
        return None;
    }
    let staged = state.pending_reboot();
    let notice = if !staged.is_empty() {
        Notice::Reboot { names: FirmwareState::names(&staged) }
    } else {
        let offers = state.offers();
        if offers.is_empty() {
            return None;
        }
        let key = state.key();
        if prefs.snoozed(&key, now).is_some() {
            return None;
        }
        Notice::Available { names: FirmwareState::names(&offers), count: offers.len() }
    };
    if prefs.notified == notice.key() && now - prefs.notified_at < RENOTIFY {
        return None;
    }
    Some(notice)
}

// ---------------------------------------------------------------------------
// Root actions: the systemd units the app may start (polkit: wheel, no password)
// ---------------------------------------------------------------------------

pub const UNIT_CHECK: &str = "pengyos-firmware-check.service";
pub const UNIT_APPLY_PREFIX: &str = "pengyos-firmware-update@";

/// The unit that installs `ids` (all of them when the list is empty). The ids are
/// comma-joined, and a comma is not allowed in a unit name, so the instance is
/// escaped the way `systemd-escape` does it (`,` -> `\x2c`); the unit passes the
/// unescaped `%I` to `pengyos-firmware apply`, which splits it back into ids.
pub fn update_unit(ids: &[String]) -> String {
    let instance = if ids.is_empty() { "all".into() } else { escape_instance(&ids.join(",")) };
    format!("{UNIT_APPLY_PREFIX}{instance}.service")
}

/// `systemd-escape` for an instance name: keep `[A-Za-z0-9:_.]` (no leading
/// `.`), write every other byte as `\xNN`.
fn escape_instance(s: &str) -> String {
    let mut out = String::new();
    for (i, b) in s.bytes().enumerate() {
        if b.is_ascii_alphanumeric() || b == b':' || b == b'_' || (b == b'.' && i > 0) {
            out.push(b as char);
        } else {
            out.push_str(&format!("\\x{b:02x}"));
        }
    }
    out
}

/// The device ids in `apply`'s arguments (what follows the subcommand): the
/// unit passes its instance name, so they arrive comma-joined, and `all` (or
/// nothing) means every device. Empty = all.
pub fn apply_ids(args: &[String]) -> Vec<String> {
    args.iter()
        .flat_map(|a| a.split(','))
        .map(str::trim)
        .filter(|a| !a.is_empty() && *a != "all" && !a.starts_with('-'))
        .map(str::to_string)
        .collect()
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
        return Err("Only an administrator of this computer can install firmware.".into());
    }
    Err(if err.is_empty() { format!("{unit} failed") } else { err })
}

/// Is an install running (started here, by the notifier, or in a terminal)?
/// Any instance of the templated unit counts, so the app follows an install it
/// did not start.
pub fn apply_running() -> bool {
    Command::new("systemctl")
        .args(["list-units", "--no-legend", "--plain", "--type=service", "pengyos-firmware-update@*.service"])
        .output()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .any(|l| l.contains("activating") || l.contains("running"))
        })
        .unwrap_or(false)
}

/// The machine's name from `/etc/os-release`, for the window title.
#[derive(Clone, Debug, Default)]
pub struct Running {
    pub name: String,
    pub version: String,
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

/// Write a line to a log for the units' journal (never fails the job).
pub fn log_line(path: &Path, line: &str) {
    if let Ok(mut f) = fs::OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{} {}", iso(now()), line);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Trimmed from a real `fwupdmgr get-devices --json` (behoserver, 2026-10-15).
    const DEVICES: &str = r#"{
      "Devices": [
        {"DeviceId":"2a4c23bfb79b5dabe474cb7b1b3e604645d6f9c6","Name":"System Firmware","Plugin":"uefi_capsule",
         "Flags":["internal","require-ac","needs-reboot","updatable","supported","signed-payload"],
         "Version":"0.1.20","VersionFormat":"version"},
        {"DeviceId":"364cc0a5c620e0e524aa15266840b1e147ed3b8f","Name":"ASUSTeK dbx Certificate","Plugin":"uefi_dbx",
         "Flags":["internal","needs-reboot","updatable-hidden"],"Version":"2011","UpdateError":"No vendor ID set"},
        {"DeviceId":"786b561d52312cd966c432e92eb4acd94a28037d","Name":"MSI DRM","Plugin":"linux_display",
         "Flags":["internal"],"Created":1791594052}
      ]
    }"#;

    const UPDATES: &str = r#"{
      "Devices": [
        {"DeviceId":"2a4c23bfb79b5dabe474cb7b1b3e604645d6f9c6","Name":"System Firmware","Plugin":"uefi_capsule",
         "Flags":["internal","require-ac","needs-reboot","updatable","supported","signed-payload"],
         "Version":"0.1.20","VersionFormat":"version",
         "Updates":[
           {"Version":"0.1.30","Description":"<p>Maintenance.</p>","Size":1048576,"Categories":["internal"]},
           {"Version":"0.1.40","Description":"<p>Fixes a boot hang.</p><ul><li>USB boot</li></ul>",
            "Size":12369504,"Urgency":"high","Categories":["security","internal"],"Flags":["is-upgrade"]}
         ]},
        {"DeviceId":"364cc0a5c620e0e524aa15266840b1e147ed3b8f","Name":"ASUSTeK dbx Certificate","Plugin":"uefi_dbx",
         "Flags":["internal","needs-reboot","updatable-hidden"],"Version":"2011",
         "Updates":[{"Version":"20260707","Size":4096,"Categories":["security"],"Urgency":"critical"}]}
      ]
    }"#;

    #[test]
    fn times_and_words() {
        assert_eq!(parse_iso("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_iso("2026-10-15T18:04:00Z"), Some(1_792_087_440));
        assert_eq!(iso(1_792_087_440), "2026-10-15T18:04:00Z");
        assert_eq!(iso(0), "1970-01-01T00:00:00Z");
        for s in [0i64, 1, 86_400, 1_792_087_440, 2_000_000_000] {
            assert_eq!(parse_iso(&iso(s)), Some(s), "round trip {s}");
        }
        assert_eq!(parse_iso("garbage"), None);
        assert_eq!(ago(100, 130), "just now");
        assert_eq!(ago(0, 3 * 60), "3 minutes ago");
        assert_eq!(ago(0, 2 * DAY + 5), "2 days ago");
        assert_eq!(until(DAY, 0), "tomorrow");
        assert_eq!(until(14 * DAY, 0), "in 2 weeks");
        assert_eq!(human_size(12_369_504), "12 MB");
        assert_eq!(human_size(2_147_483_648), "2 GB");
        assert_eq!(human_size(2048), "2 KB");
    }

    #[test]
    fn devices_parse() {
        let devs = FirmwareState::parse_fwupdmgr(DEVICES);
        assert_eq!(devs.len(), 3);
        let fw = &devs[devs.iter().position(|d| d.name == "System Firmware").unwrap()];
        assert_eq!(fw.current, "0.1.20");
        assert!(fw.updatable());
        assert!(fw.requires_ac());
        assert!(fw.needs_reboot());
        assert!(!fw.pending_reboot(), "no update attempted yet");
        assert!(fw.offer().is_none(), "get-devices alone offers nothing");
        let display = &devs[devs.iter().position(|d| d.name == "MSI DRM").unwrap()];
        assert!(!display.updatable(), "a display is not updatable");
        assert_eq!(FirmwareState::parse_fwupdmgr("not json"), Vec::<Device>::new());
        assert_eq!(FirmwareState::parse_fwupdmgr(r#"{"Devices":[{"Name":"no id"]}"#).len(), 0);
    }

    #[test]
    fn merge_offers_the_newest_release() {
        let st = FirmwareState::merge(DEVICES, UPDATES);
        let offers = st.offers();
        assert_eq!(offers.len(), 2);
        let fw = offers.iter().find(|d| d.name == "System Firmware").unwrap();
        let a = fw.offer().unwrap();
        assert_eq!(a.version, "0.1.40", "the newest release, not the first");
        assert_eq!(a.size, 12_369_504);
        assert!(a.is_security());
        assert_eq!(a.urgency_word().as_deref(), Some("Important security update"));
        assert_eq!(a.notes(), vec!["Fixes a boot hang.".to_string(), "• USB boot".to_string()]);
        assert_eq!(FirmwareState::names(&offers), "ASUSTeK dbx Certificate and System Firmware");
        assert_eq!(st.updatable().len(), 2, "the display is not updatable");
    }

    #[test]
    fn an_update_that_is_not_newer_is_not_an_offer() {
        let mut st = FirmwareState::merge(DEVICES, UPDATES);
        for d in st.devices.iter_mut() {
            if d.name == "System Firmware" {
                d.available.as_mut().unwrap().version = "0.1.20".into();
            }
        }
        assert_eq!(st.offers().len(), 1, "same version as running: nothing to do");
        // A hex version cannot be compared as decimal, so fwupd's word stands.
        st.devices.iter_mut().for_each(|d| d.current = "1E00".into());
        assert_eq!(st.offers().len(), 2);
        assert!(!versions_comparable("1E00", "0.1.20"));
        assert!(versions_comparable("1.2.3", "1.10"));
        assert!(newer("1.10", "1.2.3"));
        assert!(!newer("26.09.2", "26.09.2"));
    }

    #[test]
    fn staged_update_waits_for_a_restart() {
        let mut st = FirmwareState::merge(DEVICES, UPDATES);
        let fw = st.devices.iter_mut().find(|d| d.name == "System Firmware").unwrap();
        fw.update_state = "Pending".into();
        assert!(fw.pending_reboot());
        assert_eq!(fw.offer(), None, "not offered twice");
        assert_eq!(st.pending_reboot().len(), 1);
        let notice = decide(&st, &Prefs::default(), 1).unwrap();
        assert_eq!(notice, Notice::Reboot { names: "System Firmware".into() });
        assert!(notice.text().0.contains("Restart"));
        // A device that is not staged is not "waiting for a restart".
        let mut other = st.clone();
        other.devices.iter_mut().for_each(|d| {
            d.update_state = "Success".into();
            d.flags.retain(|f| f != "needs-reboot");
        });
        assert!(other.pending_reboot().is_empty(), "no needs-reboot flag: nothing staged");
    }

    #[test]
    fn warnings_are_the_ones_the_user_can_act_on() {
        let st = FirmwareState::merge(DEVICES, UPDATES);
        let offers = st.offers();
        let fw = offers.iter().find(|d| d.name == "System Firmware").unwrap();
        let w = fw.warnings();
        assert!(w.iter().any(|x| x.contains("wall")), "requires AC");
        assert!(w.iter().any(|x| x.contains("restart")), "needs reboot");
        let mut d = Device { flags: vec!["updatable".into(), "needs-shutdown".into(), "locked".into()], ..Default::default() };
        let w = d.warnings();
        assert!(w.iter().any(|x| x.contains("switched off")));
        assert!(!w.iter().any(|x| x.contains("at the next restart")), "shutdown, not reboot");
        d.flags.push("wait-for-replug".into());
        assert!(d.warnings().iter().any(|x| x.contains("unplug")));
    }

    #[test]
    fn state_round_trips_through_json() {
        let st = FirmwareState { checked: Some(1_792_087_440), fwupd: "2.1.1".into(), ..FirmwareState::merge(DEVICES, UPDATES) };
        let dir = std::env::temp_dir().join(format!("pengyos-firmware-test-{}", std::process::id()));
        let path = dir.join("state.json");
        st.save_to(&path).unwrap();
        let back = FirmwareState::load(&path);
        assert_eq!(back.checked, st.checked);
        assert_eq!(back.fwupd, "2.1.1");
        assert_eq!(back.devices.len(), st.devices.len());
        assert_eq!(back.key(), st.key());
        assert_eq!(back.offers().len(), 2);
        let _ = fs::remove_dir_all(dir);
        assert_eq!(FirmwareState::parse("not json"), FirmwareState::default());
        assert_eq!(FirmwareState::load(Path::new("/nonexistent-pengyos-firmware")), FirmwareState::default());
    }

    #[test]
    fn notify_rules() {
        let st = FirmwareState::merge(DEVICES, UPDATES);
        let t = 1_800_000_000;
        let mut p = Prefs::default();
        let n = decide(&st, &p, t).unwrap();
        assert_eq!(n, Notice::Available { names: "ASUSTeK dbx Certificate and System Firmware".into(), count: 2 });

        // once a day at most
        p.notified = n.key();
        p.notified_at = t;
        assert_eq!(decide(&st, &p, t + 3600), None);
        assert!(decide(&st, &p, t + DAY + 1).is_some());

        // remind me later
        p.snooze(&st.key(), 7 * DAY, t);
        assert_eq!(decide(&st, &p, t + 2 * DAY), None);
        assert!(decide(&st, &p, t + 8 * DAY).is_some());
        // ... but a newer update than the snoozed set is news
        let mut newer_st = st.clone();
        newer_st.devices.iter_mut().for_each(|d| {
            if let Some(a) = d.available.as_mut() {
                a.version = format!("{}-x", a.version);
            }
        });
        assert_ne!(newer_st.key(), st.key());
        assert!(decide(&newer_st, &p, t + 2 * DAY).is_some());

        // nothing to say
        let quiet = FirmwareState::default();
        assert_eq!(decide(&quiet, &Prefs::default(), t), None);

        // notifications off: nothing, ever (the app still shows it)
        let mut off = Prefs::default();
        off.notify = false;
        assert_eq!(decide(&st, &off, t), None);
    }

    #[test]
    fn prefs_round_trip() {
        let dir = std::env::temp_dir().join(format!("pengyos-firmware-prefs-{}", std::process::id()));
        let path = dir.join("settings.json");
        let mut p = Prefs { notify: false, ..Prefs::default() };
        p.snooze("abc:1", DAY, 10);
        p.notified = "available:X:1".into();
        p.notified_at = 20;
        p.save_to(&path).unwrap();
        assert_eq!(Prefs::load_from(&path), p);
        assert_eq!(Prefs::load_from(&dir.join("missing.json")), Prefs::default());
        assert_eq!(Prefs::parse("{}"), Prefs::default());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn progress_lines() {
        let p = Progress::default();
        let p = p.from_line("Preparing…: 0%");
        assert_eq!((p.phase.as_str(), p.fraction), ("Preparing…", 0.0));
        let p = p.from_line("Writing…: 45%");
        assert_eq!((p.phase.as_str(), p.fraction), ("Writing…", 0.45));
        let p = p.from_line("Verifying…");
        assert_eq!((p.phase.as_str(), p.fraction), ("Verifying…", 0.45), "no percent: keep the fraction");
        let p = p.from_line("");
        assert_eq!(p.phase, "Verifying…");
        let p = p.from_line("Installing…: 100%");
        assert_eq!(p.fraction, 1.0);
        // A terminal progress line arrives as one line with \r separators.
        let p = Progress::default().from_line("Downloading…: 12%\rDownloading…: 88%");
        assert_eq!((p.phase.as_str(), p.fraction), ("Downloading…", 0.88));
        let p = Progress::default().from_line("no percent here");
        assert_eq!(p.message, "no percent here");
        let dir = std::env::temp_dir().join(format!("pengyos-firmware-prog-{}", std::process::id()));
        let path = dir.join("progress.json");
        Progress { phase: "done".into(), fraction: 1.0, message: "Done".into() }.save_to(&path).unwrap();
        assert_eq!(Progress::parse(&fs::read_to_string(&path).unwrap()).unwrap().phase, "done");
        assert_eq!(Progress::parse("not json"), None);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn units() {
        assert_eq!(UNIT_CHECK, "pengyos-firmware-check.service");
        assert_eq!(update_unit(&[]), "pengyos-firmware-update@all.service");
        assert_eq!(
            update_unit(&["2a4c23bfb79b5dabe474cb7b1b3e604645d6f9c6".into()]),
            "pengyos-firmware-update@2a4c23bfb79b5dabe474cb7b1b3e604645d6f9c6.service"
        );
        // systemd rejects a comma in a unit name; this is what systemd-escape writes.
        assert_eq!(
            update_unit(&["2a4c23bf".into(), "364cc0a5".into()]),
            "pengyos-firmware-update@2a4c23bf\\x2c364cc0a5.service"
        );
    }

    #[test]
    fn apply_ids_from_the_instance() {
        let s = |v: &[&str]| v.iter().map(|x| x.to_string()).collect::<Vec<_>>();
        assert!(apply_ids(&s(&["all"])).is_empty());
        assert!(apply_ids(&[]).is_empty());
        assert_eq!(apply_ids(&s(&["2a4c23bf,364cc0a5"])), s(&["2a4c23bf", "364cc0a5"]));
    }

    #[test]
    fn running_from_os_release() {
        let r = Running::parse("NAME=\"PengyOS\"\nVARIANT=\"Desktop NVIDIA\"\nIMAGE_VERSION=\"26.10.2\"\n");
        assert_eq!((r.name.as_str(), r.version.as_str()), ("PengyOS Desktop NVIDIA", "26.10.2"));
    }
}
