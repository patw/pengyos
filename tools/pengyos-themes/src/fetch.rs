//! Downloads, over curl (in every image), with a size cap and a timeout on every
//! request so nothing can hang. HTTPS only, redirects included.
//!
//! Everything that talks to the network goes through [`Fetch`], so the tests
//! stub it and never touch the network.

use std::path::Path;
use std::process::Command;

use crate::{err, Result};

/// The User-Agent sent with every request: the program and its version, nothing
/// about the user or the machine.
pub const USER_AGENT: &str = concat!("pengyos-themes/", env!("CARGO_PKG_VERSION"));

pub trait Fetch {
    /// GET `url` into memory, refusing more than `max` bytes.
    fn get(&self, url: &str, max: u64, timeout_s: u64) -> Result<Vec<u8>>;
    /// GET `url` into the file `dest` (created or replaced), refusing more than `max` bytes.
    fn download(&self, url: &str, dest: &Path, max: u64, timeout_s: u64) -> Result<()>;
}

/// The real thing: `curl`.
pub struct Curl;

impl Curl {
    fn argv(url: &str, max: u64, timeout_s: u64) -> Vec<String> {
        vec![
            "--fail".into(),
            "--silent".into(),
            "--show-error".into(),
            "--location".into(),
            "--proto".into(),
            "=https".into(),
            "--proto-redir".into(),
            "=https".into(),
            "--connect-timeout".into(),
            "15".into(),
            "--max-time".into(),
            timeout_s.to_string(),
            "--max-filesize".into(),
            max.to_string(),
            "--user-agent".into(),
            USER_AGENT.into(),
            "--url".into(),
            url.into(),
        ]
    }

    fn run(url: &str, mut argv: Vec<String>, stdout: bool) -> Result<Vec<u8>> {
        if !url.starts_with("https://") {
            return Err(err!("refusing {url}: only https:// downloads are allowed"));
        }
        if !stdout {
            argv.push("--no-progress-meter".into());
        }
        let out = Command::new("curl")
            .args(&argv)
            .output()
            .map_err(|e| err!("cannot run curl: {e}"))?;
        if out.status.success() {
            return Ok(out.stdout);
        }
        let why = match out.status.code() {
            Some(6) | Some(7) => "no network (could not connect)".to_string(),
            Some(28) => "timed out".to_string(),
            Some(63) => "the download is larger than expected".to_string(),
            Some(22) => String::from_utf8_lossy(&out.stderr).trim().replace("curl: (22) ", ""),
            _ => String::from_utf8_lossy(&out.stderr).trim().to_string(),
        };
        Err(err!("could not download {url}: {why}"))
    }
}

impl Fetch for Curl {
    fn get(&self, url: &str, max: u64, timeout_s: u64) -> Result<Vec<u8>> {
        let body = Curl::run(url, Curl::argv(url, max, timeout_s), true)?;
        if body.len() as u64 > max {
            return Err(err!("could not download {url}: the answer is larger than expected"));
        }
        Ok(body)
    }

    fn download(&self, url: &str, dest: &Path, max: u64, timeout_s: u64) -> Result<()> {
        let mut argv = Curl::argv(url, max, timeout_s);
        argv.push("--output".into());
        argv.push(dest.to_string_lossy().into_owned());
        Curl::run(url, argv, false).map(|_| ())
    }
}

/// Percent-encode a query value (RFC 3986 unreserved characters pass).
pub fn query_escape(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

#[cfg(test)]
pub mod stub {
    //! A [`Fetch`] that serves fixed bodies by URL and records what was asked.
    use super::*;
    use std::cell::RefCell;
    use std::collections::HashMap;

    #[derive(Default)]
    pub struct Stub {
        pub bodies: HashMap<String, Vec<u8>>,
        pub asked: RefCell<Vec<String>>,
    }

    impl Stub {
        pub fn with(url: &str, body: Vec<u8>) -> Stub {
            let mut s = Stub::default();
            s.bodies.insert(url.to_string(), body);
            s
        }
    }

    impl Fetch for Stub {
        fn get(&self, url: &str, max: u64, _t: u64) -> Result<Vec<u8>> {
            self.asked.borrow_mut().push(url.to_string());
            let b = self
                .bodies
                .get(url)
                .cloned()
                .ok_or_else(|| err!("could not download {url}: no network (could not connect)"))?;
            if b.len() as u64 > max {
                return Err(err!("could not download {url}: the download is larger than expected"));
            }
            Ok(b)
        }

        fn download(&self, url: &str, dest: &Path, max: u64, t: u64) -> Result<()> {
            let b = self.get(url, max, t)?;
            std::fs::write(dest, b)?;
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn escapes_queries() {
        assert_eq!(query_escape("nord dark"), "nord%20dark");
        assert_eq!(query_escape("#ff0000"), "%23ff0000");
        assert_eq!(query_escape("a&b=c"), "a%26b%3Dc");
    }

    #[test]
    fn curl_never_leaves_https_and_always_has_a_timeout() {
        let a = Curl::argv("https://example.org/x", 10, 20);
        let s = a.join(" ");
        assert!(s.contains("--proto =https --proto-redir =https"));
        assert!(s.contains("--max-time 20"));
        assert!(s.contains("--connect-timeout 15"));
        assert!(s.contains("--max-filesize 10"));
        assert!(s.contains(&format!("--user-agent {USER_AGENT}")));
        assert!(Curl.get("http://example.org/x", 10, 20).unwrap_err().0.contains("only https://"));
        assert!(Curl.get("file:///etc/passwd", 10, 20).unwrap_err().0.contains("only https://"));
    }
}
