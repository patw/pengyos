//! Remote Desktop: the model and command lines behind the window.
//!
//! Everything comes from, and goes to, `pengyos-remote` (Desktop overlay):
//! `status --json` is the state, and on / off / password / forget are the
//! changes. They run as the person: their RDP password and certificate live in
//! their own ~/.config, so nothing here needs root.

use std::io::Write;
use std::process::{Command, Stdio};

use serde_json::Value;

#[cfg(feature = "cosmic")]
pub mod app;

pub const APP_ID: &str = "io.catbee.pengyos.RemoteDesktop";
pub const REMOTE: &str = "/usr/bin/pengyos-remote";
pub const MIN_PASSWORD: usize = 8;
pub const MAX_PASSWORD: usize = 128;

/// `pengyos-remote status --json`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Status {
    pub enabled: bool,
    /// running, stopped, starting, failed…
    pub state: String,
    pub user: String,
    pub port: u16,
    pub host: String,
    pub addresses: Vec<String>,
    pub password_set: bool,
    pub fingerprint: Option<String>,
    pub consent_saved: bool,
    pub log: Vec<String>,
}

impl Status {
    pub fn parse(json: &str) -> Result<Status, String> {
        let v: Value = serde_json::from_str(json).map_err(|e| format!("pengyos-remote said something unexpected: {e}"))?;
        let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or_default().to_string();
        let b = |k: &str| v.get(k).and_then(Value::as_bool).unwrap_or(false);
        let list = |k: &str| {
            v.get(k)
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect())
                .unwrap_or_default()
        };
        Ok(Status {
            enabled: b("enabled"),
            state: s("state"),
            user: s("user"),
            port: v.get("port").and_then(Value::as_u64).unwrap_or(3389) as u16,
            host: s("host"),
            addresses: list("addresses"),
            password_set: b("password_set"),
            fingerprint: v.get("fingerprint").and_then(Value::as_str).map(String::from),
            consent_saved: b("consent_saved"),
            log: list("log"),
        })
    }

    pub fn running(&self) -> bool {
        self.state == "running"
    }

    /// What to type into a client, best first: the name, then each address.
    pub fn connect_to(&self) -> Vec<String> {
        let mut out = Vec::new();
        if !self.host.is_empty() {
            out.push(join_port(&self.host, self.port));
        }
        out.extend(self.addresses.iter().map(|a| join_port(a, self.port)));
        out
    }

    /// One sentence on where things stand.
    pub fn headline(&self) -> String {
        match (self.enabled, self.running()) {
            (false, _) => "Remote desktop is off".into(),
            (true, true) => "Remote desktop is on".into(),
            (true, false) if self.state == "failed" => "Remote desktop is on, but its server stopped".into(),
            (true, false) => "Remote desktop is on, and starts when you log in".into(),
        }
    }
}

fn join_port(host: &str, port: u16) -> String {
    let host = if host.contains(':') { format!("[{host}]") } else { host.to_string() };
    if port == 3389 { host } else { format!("{host}:{port}") }
}

pub fn status_argv() -> Vec<String> {
    vec![REMOTE.into(), "status".into(), "--json".into()]
}

pub fn on_argv() -> Vec<String> {
    vec![REMOTE.into(), "on".into()]
}

pub fn off_argv() -> Vec<String> {
    vec![REMOTE.into(), "off".into()]
}

pub fn forget_argv() -> Vec<String> {
    vec![REMOTE.into(), "forget".into()]
}

/// The password goes on stdin, never on a command line (other users see those).
pub fn password_argv() -> Vec<String> {
    vec![REMOTE.into(), "password".into(), "--stdin".into()]
}

pub fn password_problem(pw: &str, again: &str) -> Option<String> {
    let n = pw.chars().count();
    if n < MIN_PASSWORD {
        return Some(format!("Use at least {MIN_PASSWORD} characters."));
    }
    if n > MAX_PASSWORD {
        return Some(format!("Use at most {MAX_PASSWORD} characters."));
    }
    if pw.contains(['\n', '\r']) {
        return Some("The password cannot contain a line break.".into());
    }
    if pw != again {
        return Some("The two passwords are different.".into());
    }
    None
}

/// pengyos-remote's own message, without its name, as a sentence.
pub fn failure(stderr: &str) -> String {
    let msg = stderr
        .lines()
        .map(|l| l.trim_start_matches("pengyos-remote:").trim())
        .filter(|l| !l.is_empty())
        .last()
        .unwrap_or("pengyos-remote failed")
        .to_string();
    let mut c = msg.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => msg,
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
        Err(failure(&String::from_utf8_lossy(&out.stderr)))
    }
}

pub fn status() -> Result<Status, String> {
    Status::parse(&run(&status_argv(), None)?)
}

#[cfg(test)]
mod tests;
