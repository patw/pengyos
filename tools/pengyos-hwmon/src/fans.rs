//! The Fans pane: what `pengyos-fans status --json` reports, for both front-ends.
//!
//! The Hardware Monitor never writes a fan setting itself. Reading is done by
//! running `pengyos-fans` as the user (it only reads sysfs and its map); a change
//! from the window or the panel applet runs `pkexec /usr/bin/pengyos-fans ...`.
//! The Desktop's polkit rule (60-pengyos-fans.rules) lets an administrator at the
//! computer do that without a password; anyone else is asked for one. The
//! terminal view only shows the commands to type.

use serde_json::Value;
use std::process::Command;

pub const FANS: &str = "/usr/bin/pengyos-fans";
#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub const PROFILES: [&str; 4] = ["quiet", "balanced", "full", "firmware"];
/// What a fan's speed can follow, as `pengyos-fans follow` spells it.
#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub const FOLLOWS: [&str; 4] = ["chip", "cpu", "gpu", "cpu+gpu"];

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
    /// "fan", "pump" or "none"
    pub role: String,
    pub follow: String,
    /// The curve it follows now (°C, %), None under firmware or when left alone.
    pub curve: Option<Vec<(i64, i64)>>,
    pub own_curve: bool,
    /// The chip runs its curve itself (and so can follow "chip").
    pub chip_curve: bool,
    pub max_points: usize,
    pub min_duty: i64,
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
                    role: text(r, "role"),
                    follow: text(r, "follow"),
                    curve: r.get("curve").and_then(Value::as_array).map(|pts| {
                        pts.iter()
                            .filter_map(|p| Some((p.get(0)?.as_i64()?, p.get(1)?.as_i64()?)))
                            .collect()
                    }),
                    own_curve: r.get("own_curve").and_then(Value::as_bool).unwrap_or(false),
                    chip_curve: r
                        .get("chip_curve")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    max_points: r.get("max_points").and_then(Value::as_u64).unwrap_or(8) as usize,
                    min_duty: r.get("min_duty").and_then(Value::as_i64).unwrap_or(20),
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
pub fn name_argv(id: &str, name: &str) -> Vec<String> {
    vec![
        "pkexec".into(),
        FANS.into(),
        "name".into(),
        id.into(),
        name.into(),
    ]
}

#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub fn role_argv(id: &str, role: &str) -> Vec<String> {
    vec![
        "pkexec".into(),
        FANS.into(),
        "role".into(),
        id.into(),
        role.into(),
    ]
}

#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub fn follow_argv(id: &str, follow: &str) -> Vec<String> {
    vec![
        "pkexec".into(),
        FANS.into(),
        "follow".into(),
        id.into(),
        follow.into(),
    ]
}

/// `curve ID 30:25 60:45 ...`, or `curve ID default` for an empty list.
#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub fn curve_argv(id: &str, points: &[(i64, i64)]) -> Vec<String> {
    let mut v: Vec<String> = vec!["pkexec".into(), FANS.into(), "curve".into(), id.into()];
    if points.is_empty() {
        v.push("default".into());
    } else {
        v.extend(points.iter().map(|(t, d)| format!("{t}:{d}")));
    }
    v
}

/// Check a curve the way `pengyos-fans curve` will, so the window can say what is
/// wrong before asking for anything. `max` is the chip's point count.
#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub fn check_curve(points: &[(i64, i64)], max: usize) -> Result<(), String> {
    if points.len() < 2 || points.len() > max.min(8) {
        return Err(format!("A curve has 2 to {} points.", max.min(8)));
    }
    if points
        .windows(2)
        .any(|w| w[1].0 <= w[0].0 || w[1].1 < w[0].1)
    {
        return Err(
            "Temperatures must rise, and speeds must not fall, from point to point.".into(),
        );
    }
    if points
        .iter()
        .any(|&(t, d)| !(0..=100).contains(&t) || !(0..=100).contains(&d))
    {
        return Err("Temperatures 0-100 °C, speeds 0-100 %.".into());
    }
    if points.last().map(|p| p.1) != Some(100) {
        return Err(
            "The last point must be 100 %, so a hot machine always gets full speed.".into(),
        );
    }
    Ok(())
}

/// Run several commands in order, stopping at the first that fails.
#[cfg_attr(not(feature = "cosmic"), allow(dead_code))]
pub fn run_all(commands: &[Vec<String>]) -> Result<(), String> {
    commands.iter().try_for_each(|argv| run(argv))
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
            Err("Not changed: this needs an administrator.".into())
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
    fn reads_curves_and_limits() {
        let s = parse(
            r#"{"profile":"quiet","kind":"channels","undetected":[],"platform_profile":null,"channels":[
              {"id":"nct6797:pwm4","name":"Rear","role":"fan","fan":"fan4","follow":"chip","rpm":700,"duty":45,
               "control":"chip curve, own curve","min_duty":45,"own_curve":true,"chip_curve":true,"max_points":4,
               "curve":[[30,45],[60,60],[80,100]]}]}"#,
        )
        .unwrap();
        let c = &s.channels[0];
        assert_eq!(
            c.curve.as_deref(),
            Some(&[(30, 45), (60, 60), (80, 100)][..])
        );
        assert!(c.own_curve && c.chip_curve);
        assert_eq!(
            (c.max_points, c.min_duty, c.follow.as_str()),
            (4, 45, "chip")
        );
    }

    #[test]
    fn edit_commands() {
        assert_eq!(
            curve_argv("nct6798:pwm1", &[(30, 25), (80, 100)]),
            [
                "pkexec",
                "/usr/bin/pengyos-fans",
                "curve",
                "nct6798:pwm1",
                "30:25",
                "80:100"
            ]
        );
        assert_eq!(curve_argv("a:pwm1", &[])[4], "default");
        assert_eq!(name_argv("a:pwm1", "Front intake")[4], "Front intake");
        assert_eq!(follow_argv("a:pwm1", "cpu+gpu")[3..], ["a:pwm1", "cpu+gpu"]);
    }

    #[test]
    fn curves_are_checked_like_the_tool_does() {
        assert!(check_curve(&[(30, 25), (60, 45), (80, 100)], 4).is_ok());
        assert!(check_curve(&[(30, 25)], 4).is_err());
        assert!(check_curve(&[(60, 25), (40, 50), (80, 100)], 4).is_err());
        assert!(check_curve(&[(30, 50), (60, 40), (80, 100)], 4).is_err());
        assert!(check_curve(&[(30, 25), (80, 90)], 4).is_err());
        assert!(check_curve(&[(10, 20), (20, 30), (30, 40), (40, 50), (80, 100)], 4).is_err());
        assert!(check_curve(&[(10, 20), (20, 30), (30, 40), (40, 50), (80, 100)], 8).is_ok());
    }

    #[test]
    fn garbage_is_an_error_not_a_panic() {
        assert!(parse("not json").is_err());
    }
}
