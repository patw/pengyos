//! `pengyos-sharing` — the "File Sharing" app's logic, std + serde_json only.
//!
//! The app is a window over `pengyos-shares`, which does the work as root: it
//! writes ksmbd's configuration, keeps the share passwords, starts and stops
//! ksmbd and opens port 445 to the LAN through pengyos-firewall. This crate reads
//! its `status --json`, checks what the person typed the same way the CLI does
//! (so a bad name is explained before anything runs), and builds the
//! `pkexec /usr/bin/pengyos-shares …` command lines. Administrators (wheel) run
//! them without a password (`60-pengyos-shares.rules`); anyone else is asked for
//! an administrator's.

use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

#[cfg(feature = "cosmic")]
pub mod app;

pub const APP_ID: &str = "io.catbee.pengyos.Sharing";

/// The program the polkit rule allows, by absolute path (pkexec matches on it).
pub const SHARES: &str = "/usr/bin/pengyos-shares";

/// Share names: what `pengyos-shares` accepts (its NAME_RE and reserved list).
pub const NAME_MAX: usize = 32;
const RESERVED: [&str; 4] = ["global", "homes", "printers", "ipc$"];
/// Folders that are never shared (the CLI refuses them too).
const SYSTEM_DIRS: [&str; 8] = ["/usr", "/etc", "/boot", "/proc", "/sys", "/dev", "/run", "/efi"];
pub const PASSWORD_MIN: usize = 8;
pub const PASSWORD_MAX: usize = 128;

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Share {
    pub name: String,
    pub path: String,
    pub read_only: bool,
    pub users: Vec<String>,
    pub comment: String,
}

/// What `pengyos-shares status --json` says.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct State {
    /// ksmbd is running (it is whenever there is at least one share).
    pub active: bool,
    pub enabled: bool,
    /// The name other computers use (`\\host\share`).
    pub host: String,
    pub shares: Vec<Share>,
    /// Who has a share password; None when we could not read it (not root).
    pub users: Option<Vec<String>>,
}

impl State {
    pub fn parse(text: &str) -> Result<State, String> {
        let v: serde_json::Value =
            serde_json::from_str(text).map_err(|e| format!("pengyos-shares answered something unreadable: {e}"))?;
        let s = |v: &serde_json::Value| v.as_str().unwrap_or_default().to_string();
        let list = |v: &serde_json::Value| -> Vec<String> {
            v.as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default()
        };
        let shares = v["shares"]
            .as_array()
            .map(|a| {
                a.iter()
                    .map(|x| Share {
                        name: s(&x["name"]),
                        path: s(&x["path"]),
                        read_only: x["read_only"].as_bool().unwrap_or(false),
                        users: list(&x["users"]),
                        comment: s(&x["comment"]),
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(State {
            active: v["active"].as_bool().unwrap_or(false),
            enabled: v["enabled"].as_bool().unwrap_or(false),
            host: s(&v["host"]),
            shares,
            users: if v["users"].is_null() { None } else { Some(list(&v["users"])) },
        })
    }

    /// Has `user` a share password? None = unknown (status ran without root).
    pub fn has_password(&self, user: &str) -> Option<bool> {
        self.users.as_ref().map(|u| u.iter().any(|x| x == user))
    }

    pub fn share(&self, name: &str) -> Option<&Share> {
        self.shares.iter().find(|s| s.name == name)
    }
}

/// How other computers open a share: (Windows, macOS/Linux).
pub fn addresses(host: &str, name: &str) -> (String, String) {
    (format!("\\\\{host}\\{name}"), format!("smb://{host}/{name}"))
}

/// One line about a share: "read-write · pat" / "read-only · pat, sam".
pub fn describe(s: &Share) -> String {
    let mode = if s.read_only { "Read-only" } else { "Read and write" };
    match s.users.len() {
        0 => format!("{mode} · nobody"),
        _ => format!("{mode} · {}", s.users.join(", ")),
    }
}

/// Why `name` is not a share name, or None. Mirrors pengyos-shares' check, and
/// refuses a name another share already uses in any letter case (SMB names are
/// case-insensitive) unless it is that same share being changed.
pub fn name_problem(name: &str, existing: &[Share]) -> Option<String> {
    if name.is_empty() {
        return Some("Give the share a name.".into());
    }
    let mut chars = name.chars();
    let first_ok = chars.next().is_some_and(|c| c.is_ascii_alphanumeric());
    let rest_ok = chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'));
    if !first_ok || !rest_ok || name.len() > NAME_MAX {
        return Some(format!(
            "Use letters, digits, dots, dashes and underscores (up to {NAME_MAX}), starting with a letter or digit."
        ));
    }
    if RESERVED.contains(&name.to_ascii_lowercase().as_str()) {
        return Some(format!("{name} is a name SMB reserves. Pick another."));
    }
    if let Some(s) = existing.iter().find(|s| s.name.eq_ignore_ascii_case(name) && s.name != name) {
        return Some(format!("{} is already a share name.", s.name));
    }
    None
}

/// Why `path` cannot be shared, or None.
pub fn path_problem(path: &str) -> Option<String> {
    if path.is_empty() {
        return Some("Choose a folder.".into());
    }
    let p = Path::new(path);
    if !p.is_absolute() {
        return Some("Choose a folder (a full path, starting with /).".into());
    }
    if path == "/" || SYSTEM_DIRS.iter().any(|d| path == *d || path.starts_with(&format!("{d}/"))) {
        return Some("System folders cannot be shared.".into());
    }
    if !p.is_dir() {
        return Some(format!("{path} is not a folder on this computer."));
    }
    None
}

/// A share name suggested from a folder: its last part, made valid.
pub fn suggest_name(path: &str) -> String {
    let base = Path::new(path).file_name().and_then(|n| n.to_str()).unwrap_or("");
    let mut out: String = base
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') { c } else { '-' })
        .collect();
    while out.starts_with(|c: char| !c.is_ascii_alphanumeric()) {
        out.remove(0);
    }
    out.truncate(NAME_MAX);
    if out.is_empty() || RESERVED.contains(&out.to_ascii_lowercase().as_str()) {
        "share".into()
    } else {
        out
    }
}

/// Why this new share password will not do, or None.
pub fn password_problem(pw: &str, again: &str) -> Option<String> {
    if pw.len() < PASSWORD_MIN {
        return Some(format!("Use at least {PASSWORD_MIN} characters."));
    }
    if pw.len() > PASSWORD_MAX {
        return Some(format!("Use at most {PASSWORD_MAX} characters."));
    }
    if pw.contains('\n') {
        return Some("A password cannot contain a line break.".into());
    }
    if pw != again {
        return Some("The two passwords are different.".into());
    }
    None
}

/// `pkexec pengyos-shares add NAME PATH [--read-only] --user U…`
pub fn add_argv(name: &str, path: &str, read_only: bool, users: &[String]) -> Vec<String> {
    let mut v: Vec<String> = vec!["pkexec".into(), SHARES.into(), "add".into(), name.into(), path.into()];
    if read_only {
        v.push("--read-only".into());
    }
    for u in users {
        v.push("--user".into());
        v.push(u.clone());
    }
    v
}

pub fn remove_argv(name: &str) -> Vec<String> {
    vec!["pkexec".into(), SHARES.into(), "remove".into(), name.into()]
}

/// The password goes on stdin (never on argv, where `ps` would show it).
pub fn passwd_argv(user: &str) -> Vec<String> {
    vec!["pkexec".into(), SHARES.into(), "passwd".into(), user.into(), "--stdin".into()]
}

/// status: through pkexec for an administrator (so the password list is
/// there), plainly otherwise (everything but who has a password).
pub fn status_argv(admin: bool) -> Vec<String> {
    let mut v: Vec<String> = Vec::new();
    if admin {
        v.push("pkexec".into());
    }
    v.extend([SHARES.into(), "status".into(), "--json".into()]);
    v
}

/// What to tell the person when a command failed. pkexec says 126 when the
/// person dismissed (or failed) the authentication and 127 when not allowed.
pub fn failure(code: Option<i32>, stderr: &str) -> String {
    match code {
        Some(126) | Some(127) if !stderr.contains("pengyos-shares:") => {
            "Not allowed: sharing folders needs an administrator.".into()
        }
        _ => {
            let msg = stderr
                .lines()
                .map(|l| l.trim_start_matches("pengyos-shares:").trim())
                .filter(|l| !l.is_empty())
                .last()
                .unwrap_or("pengyos-shares failed")
                .to_string();
            let mut c = msg.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => msg,
            }
        }
    }
}

/// Run one command line, `stdin` written to it; Ok(stdout) or Err(why).
pub fn run(argv: &[String], stdin: Option<&str>) -> Result<String, String> {
    let mut child = Command::new(&argv[0])
        .args(&argv[1..])
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Could not run {}: {e}", argv[0]))?;
    if let (Some(text), Some(mut pipe)) = (stdin, child.stdin.take()) {
        let _ = pipe.write_all(text.as_bytes());
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        Err(failure(out.status.code(), &String::from_utf8_lossy(&out.stderr)))
    }
}

pub fn status(admin: bool) -> Result<State, String> {
    State::parse(&run(&status_argv(admin), None)?)
}

/// The person running the app (their login name).
pub fn current_user() -> String {
    if let Ok(u) = std::env::var("USER") {
        if !u.is_empty() {
            return u;
        }
    }
    let uid = proc_status_field("Uid:").and_then(|f| f.split_whitespace().next().map(String::from));
    uid.and_then(|uid| {
        std::fs::read_to_string("/etc/passwd").ok()?.lines().find_map(|l| {
            let f: Vec<&str> = l.split(':').collect();
            (f.len() > 2 && f[2] == uid).then(|| f[0].to_string())
        })
    })
    .unwrap_or_default()
}

fn proc_status_field(key: &str) -> Option<String> {
    std::fs::read_to_string("/proc/self/status")
        .ok()?
        .lines()
        .find_map(|l| l.strip_prefix(key).map(|v| v.trim().to_string()))
}

/// Is `gid` among the supplementary groups in a /proc/self/status text?
pub fn has_group(status: &str, gid: u32) -> bool {
    status
        .lines()
        .find_map(|l| l.strip_prefix("Groups:"))
        .is_some_and(|g| g.split_whitespace().any(|x| x.parse() == Ok(gid)))
}

/// The gid of `name` in an /etc/group text.
pub fn group_id(group_file: &str, name: &str) -> Option<u32> {
    group_file.lines().find_map(|l| {
        let f: Vec<&str> = l.split(':').collect();
        (f.len() > 2 && f[0] == name).then(|| f[2].parse().ok()).flatten()
    })
}

/// An administrator (wheel): changes need no password, and status shows who
/// has a share password.
pub fn is_admin() -> bool {
    // root (the live session runs as root) can always use pkexec.
    if proc_status_field("Uid:").is_some_and(|u| u.split_whitespace().next() == Some("0")) {
        return true;
    }
    let Some(gid) = std::fs::read_to_string("/etc/group").ok().and_then(|g| group_id(&g, "wheel")) else {
        return false;
    };
    std::fs::read_to_string("/proc/self/status").is_ok_and(|s| has_group(&s, gid))
}

#[cfg(test)]
mod tests;
