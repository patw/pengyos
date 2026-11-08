//! The Fans pane: what `pengyos-fans status --json` reports, for both front-ends.
//!
//! The Hardware Monitor never writes a fan setting itself. Reading is done by
//! running `pengyos-fans` as the user (it only reads sysfs and its map); a change
//! from the window runs `pkexec /usr/bin/pengyos-fans ...`, which asks for an
//! administrator's password (pkexec's default: no polkit rule grants it). The
//! terminal view only shows the commands to type.

use serde_json::Value;
use std::process::Command;

pub const FANS: &str = "/usr/bin/pengyos-fans";
#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub const PROFILES: [&str; 4] = ["quiet", "balanced", "full", "firmware"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Kind {
    /// Fan controls the tool can drive (mapped or not yet).
    Channels,
    /// A laptop: the firmware's profiles only.
    Platform,
    /// Nothing to control.
    Nothing,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Channel {
    pub id: String,
    pub name: String,
    pub fan: Option<String>,
    pub rpm: Option<i64>,
    pub duty: Option<i64>,
    pub control: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FanStatus {
    pub kind: Kind,
    pub profile: String,
    pub channels: Vec<Channel>,
    pub undetected: Vec<String>,
    /// (current, choices) on a laptop
    pub platform: Option<(String, Vec<String>)>,
}

fn text(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or("").to_owned()
}

pub fn parse(json: &str) -> Result<FanStatus, String> {
    let v: Value = serde_json::from_str(json).map_err(|e| format!("unreadable status: {e}"))?;
    let kind = match v.get("kind").and_then(Value::as_str) {
        Some("channels") => Kind::Channels,
        Some("platform") => Kind::Platform,
        _ => Kind::Nothing,
    };
    let channels = v
        .get("channels")
        .and_then(Value::as_array)
        .map(|rows| {
            rows.iter()
                .map(|r| Channel {
                    id: text(r, "id"),
                    name: text(r, "name"),
                    fan: r.get("fan").and_then(Value::as_str).map(str::to_owned),
                    rpm: r.get("rpm").and_then(Value::as_i64),
                    duty: r.get("duty").and_then(Value::as_i64),
                    control: text(r, "control"),
                })
                .collect()
        })
        .unwrap_or_default();
    let undetected = v
        .get("undetected")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default();
    let platform = v.get("platform_profile").filter(|p| !p.is_null()).map(|p| {
        let choices = p
            .get("choices")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default();
        (text(p, "current"), choices)
    });
    Ok(FanStatus {
        kind,
        profile: text(&v, "profile"),
        channels,
        undetected,
        platform,
    })
}

/// Ask `pengyos-fans` (as this user: it only reads). `PENGYOS_FANS` names another
/// program to ask instead (a test double; changes always run the real one).
pub fn query() -> Result<FanStatus, String> {
    let program = std::env::var("PENGYOS_FANS").unwrap_or_else(|_| FANS.to_owned());
    let out = Command::new(program)
        .args(["status", "--json"])
        .output()
        .map_err(|_| "Fan control (pengyos-fans) is not part of this system.".to_owned())?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_owned());
    }
    parse(&String::from_utf8_lossy(&out.stdout))
}

/// One sentence on what this machine allows.
pub fn summary(s: &FanStatus) -> String {
    match s.kind {
        Kind::Nothing => "This machine's fans are run by its firmware; there is nothing to control.".into(),
        Kind::Platform => {
            let (current, choices) = s.platform.clone().unwrap_or_default();
            format!(
                "The firmware runs the fans. Its profile: {current} (offered: {}).",
                choices.join(", ")
            )
        }
        Kind::Channels if s.channels.is_empty() => {
            "Fan controls found. Detect first, to learn which control drives which fan (each fan spins up for a few seconds).".into()
        }
        Kind::Channels => format!("Profile: {}.", s.profile),
    }
}

pub fn rpm_text(c: &Channel) -> String {
    c.rpm
        .map(|r| format!("{r} RPM"))
        .unwrap_or_else(|| "-".into())
}

pub fn duty_text(c: &Channel) -> String {
    c.duty
        .map(|d| format!("{d}%"))
        .unwrap_or_else(|| "-".into())
}

/// The terminal view's lines: a summary, the table, and the commands to type.
pub fn lines(s: &FanStatus) -> Vec<String> {
    let mut out = vec![summary(s), String::new()];
    if !s.channels.is_empty() {
        let mut rows = vec![[
            "CHANNEL".to_owned(),
            "NAME".into(),
            "FAN".into(),
            "SPEED".into(),
            "DUTY".into(),
            "CONTROL".into(),
        ]];
        for c in &s.channels {
            rows.push([
                c.id.clone(),
                if c.name.is_empty() {
                    "-".into()
                } else {
                    c.name.clone()
                },
                c.fan.clone().unwrap_or_else(|| "-".into()),
                rpm_text(c),
                duty_text(c),
                c.control.clone(),
            ]);
        }
        let widths: Vec<usize> = (0..5)
            .map(|i| rows.iter().map(|r| r[i].chars().count()).max().unwrap_or(0))
            .collect();
        for r in &rows {
            let mut line = String::new();
            for (i, w) in widths.iter().enumerate() {
                line.push_str(&format!("{:<w$}  ", r[i], w = w));
            }
            line.push_str(&r[5]);
            out.push(line);
        }
        out.push(String::new());
    }
    if !s.undetected.is_empty() {
        out.push(format!("Not mapped yet: {}", s.undetected.join(", ")));
    }
    match s.kind {
        Kind::Channels if s.channels.is_empty() || !s.undetected.is_empty() => {
            out.push("Map them:        sudo pengyos-fans detect".into());
            if !s.channels.is_empty() {
                out.push(
                    "Change profile:  sudo pengyos-fans profile quiet|balanced|full|firmware"
                        .into(),
                );
            }
        }
        Kind::Channels => {
            out.push(
                "Change profile:  sudo pengyos-fans profile quiet|balanced|full|firmware".into(),
            );
            out.push("Per fan:         sudo pengyos-fans name|role|follow CHANNEL ...".into());
        }
        Kind::Platform => {
            out.push("Change profile:  sudo pengyos-fans profile quiet|balanced|full".into())
        }
        Kind::Nothing => {}
    }
    out
}

#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
/// The window's commands. pkexec asks for an administrator's password.
pub fn profile_argv(name: &str) -> Vec<String> {
    vec!["pkexec".into(), FANS.into(), "profile".into(), name.into()]
}

#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub fn detect_argv() -> Vec<String> {
    vec!["pkexec".into(), FANS.into(), "detect".into()]
}

#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
/// Run one of the argv above; Err carries what the user should see.
pub fn run(argv: &[String]) -> Result<(), String> {
    let out = Command::new(&argv[0])
        .args(&argv[1..])
        .output()
        .map_err(|e| format!("{}: {e}", argv[0]))?;
    match out.status.code() {
        Some(0) => Ok(()),
        // pkexec: 126 = the password dialog was dismissed, 127 = not authorized
        Some(126) | Some(127) if argv[0] == "pkexec" => {
            Err("Not changed: no administrator password given.".into())
        }
        _ => {
            let err = String::from_utf8_lossy(&out.stderr).trim().to_owned();
            Err(if err.is_empty() {
                "pengyos-fans failed".into()
            } else {
                err
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BEHOPC: &str = r#"{
      "profile": "quiet", "profiles": ["quiet","balanced","full","firmware"],
      "channels": [
        {"id": "nct6798:pwm1", "name": "CPU", "role": "fan", "fan": "fan1", "follow": "chip",
         "present": true, "rpm": 538, "duty": 23, "control": "chip curve (CPUTIN)"},
        {"id": "nct6798:pwm6", "name": "", "role": "none", "fan": null, "follow": "chip",
         "present": true, "rpm": null, "duty": 100, "control": "left alone"}
      ],
      "undetected": [], "platform_profile": null, "kind": "channels"}"#;

    #[test]
    fn parses_a_board() {
        let s = parse(BEHOPC).unwrap();
        assert_eq!(s.kind, Kind::Channels);
        assert_eq!(s.profile, "quiet");
        assert_eq!(s.channels[0].rpm, Some(538));
        assert_eq!(s.channels[1].fan, None);
        assert_eq!(summary(&s), "Profile: quiet.");
        let l = lines(&s);
        assert!(l[2].starts_with("CHANNEL"));
        assert!(
            l[3].contains("CPU")
                && l[3].contains("538 RPM")
                && l[3].ends_with("chip curve (CPUTIN)")
        );
        assert!(l[4].contains("nct6798:pwm6  -   ") && l[4].ends_with("left alone"));
        assert!(l.iter().any(|x| x.contains("sudo pengyos-fans profile")));
        assert!(l.iter().all(|x| x.chars().count() <= 80), "{l:#?}");
    }

    #[test]
    fn parses_a_laptop_and_a_mini_pc() {
        let laptop = parse(
            r#"{"profile":"firmware","channels":[],"undetected":[],"kind":"platform",
                "platform_profile":{"current":"balanced","choices":["low-power","balanced","performance"]}}"#,
        )
        .unwrap();
        assert_eq!(laptop.kind, Kind::Platform);
        assert!(summary(&laptop).contains("balanced (offered: low-power, balanced, performance)"));
        let mini = parse(r#"{"profile":"firmware","channels":[],"undetected":[],"kind":"none","platform_profile":null}"#).unwrap();
        assert_eq!(mini.kind, Kind::Nothing);
        assert_eq!(lines(&mini).len(), 2);
    }

    #[test]
    fn says_to_detect_first() {
        let s = parse(r#"{"profile":"firmware","channels":[],"undetected":["nct6797:pwm2"],"kind":"channels","platform_profile":null}"#).unwrap();
        assert!(summary(&s).starts_with("Fan controls found. Detect first"));
        assert!(lines(&s)
            .iter()
            .any(|l| l.contains("sudo pengyos-fans detect")));
    }

    #[test]
    fn the_window_goes_through_pkexec_with_the_absolute_path() {
        assert_eq!(
            profile_argv("quiet"),
            ["pkexec", "/usr/bin/pengyos-fans", "profile", "quiet"]
        );
        assert_eq!(detect_argv(), ["pkexec", "/usr/bin/pengyos-fans", "detect"]);
    }

    #[test]
    fn garbage_is_an_error_not_a_panic() {
        assert!(parse("not json").is_err());
    }
}
