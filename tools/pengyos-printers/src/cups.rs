//! Talking to CUPS: IPP over HTTP on its local socket (`/run/cups/cups.sock`,
//! which also starts cupsd through `cups.socket`).
//!
//! Std only. Requests carry `Authorization: PeerCred <user>`: on the local
//! socket cupsd checks that name against the connecting process's uid
//! (SO_PEERCRED), so a member of a CUPS admin group (`wheel` on PengyOS —
//! `SystemGroup` in cups-files.conf) adds and removes printers with no password
//! prompt, and anyone else gets "not authorized". The HTTP framing
//! ([`parse_http`]) is pure and tested.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

use crate::ipp::{Request, Response};
use crate::{Device, Driver, Job, Printer, Snapshot};

pub const SOCKET: &str = "/run/cups/cups.sock";

#[derive(Clone, Debug)]
pub struct Cups {
    pub socket: PathBuf,
    pub user: String,
}

impl Default for Cups {
    fn default() -> Self {
        Cups::new()
    }
}

impl Cups {
    pub fn new() -> Cups {
        // CUPS_SERVER may name another socket (the libcups convention); a
        // host name there is a remote server, which this app does not manage.
        let socket = std::env::var("CUPS_SERVER")
            .ok()
            .filter(|s| s.starts_with('/'))
            .unwrap_or_else(|| SOCKET.to_string());
        Cups { socket: socket.into(), user: current_user() }
    }

    /// One IPP request on its own connection. `resource` is the HTTP path
    /// CUPS checks its policy against (`/`, `/admin/`, `/printers/NAME`).
    pub fn send(&self, resource: &str, req: &Request, timeout: Duration) -> Result<Response, String> {
        let body = req.encode();
        let mut s = UnixStream::connect(&self.socket).map_err(|e| {
            format!("Cannot reach the printing service (CUPS) at {}: {e}", self.socket.display())
        })?;
        let _ = s.set_read_timeout(Some(timeout));
        let _ = s.set_write_timeout(Some(timeout));
        let head = format!(
            "POST {resource} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/ipp\r\n\
             Content-Length: {}\r\nAuthorization: PeerCred {}\r\nConnection: close\r\n\r\n",
            body.len(),
            self.user
        );
        s.write_all(head.as_bytes())
            .and_then(|()| s.write_all(&body))
            .map_err(|e| format!("Sending to CUPS failed: {e}"))?;
        let mut raw = Vec::new();
        s.read_to_end(&mut raw).map_err(|e| match e.kind() {
            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut => "CUPS did not answer in time.".to_string(),
            _ => format!("Reading CUPS' answer failed: {e}"),
        })?;
        let (code, body) = parse_http(&raw)?;
        match code {
            200 => Response::decode(&body).map_err(|e| e.to_string()),
            401 | 403 => Err("Only administrators (members of the wheel group) can change printers.".into()),
            c => Err(format!("CUPS answered HTTP {c}.")),
        }
    }

    fn call(&self, resource: &str, req: &Request, what: &str) -> Result<Response, String> {
        let r = self.send(resource, req, Duration::from_secs(30))?;
        if r.ok() {
            Ok(r)
        } else {
            Err(crate::explain(r.status, r.message().as_deref(), what))
        }
    }

    pub fn printers(&self) -> Result<Vec<Printer>, String> {
        let default = self
            .send("/", &crate::req_default(&self.user), Duration::from_secs(10))
            .ok()
            .and_then(|r| crate::read_default(&r));
        let r = self.send("/", &crate::req_printers(&self.user), Duration::from_secs(10))?;
        // No printers at all is "not found", not an error.
        if !r.ok() && r.status != crate::ipp::status::NOT_FOUND {
            return Err(crate::explain(r.status, r.message().as_deref(), "list the printers"));
        }
        Ok(crate::read_printers(&r, default.as_deref()))
    }

    pub fn jobs(&self, which: &str, limit: Option<i32>) -> Result<Vec<Job>, String> {
        let r = self.send("/", &crate::req_jobs(&self.user, which, limit), Duration::from_secs(10))?;
        if !r.ok() && r.status != crate::ipp::status::NOT_FOUND {
            return Err(crate::explain(r.status, r.message().as_deref(), "read the print queue"));
        }
        Ok(crate::read_jobs(&r))
    }

    pub fn snapshot(&self) -> Result<Snapshot, String> {
        let printers = self.printers()?;
        let queue = self.jobs("not-completed", None)?;
        // CUPS keeps finished jobs for a while (job history); the newest few.
        let mut history = self.jobs("completed", None).unwrap_or_default();
        history.truncate(5);
        Ok(Snapshot { printers, queue, history })
    }

    /// The printers CUPS' backends can find (USB, mDNS on the network, HPLIP).
    /// Admin-only in CUPS' policy, like adding.
    pub fn devices(&self, timeout_s: i32) -> Result<Vec<Device>, String> {
        let r = self.send(
            "/admin/",
            &crate::req_devices(&self.user, timeout_s),
            Duration::from_secs(timeout_s as u64 + 30),
        )?;
        if !r.ok() {
            return Err(crate::explain(r.status, r.message().as_deref(), "look for printers"));
        }
        Ok(crate::read_devices(&r))
    }

    /// Every driver CUPS has (several thousand with Gutenprint and HPLIP).
    pub fn drivers(&self) -> Result<Vec<Driver>, String> {
        let r = self.send("/", &crate::req_drivers(&self.user), Duration::from_secs(90))?;
        if !r.ok() {
            return Err(crate::explain(r.status, r.message().as_deref(), "list the printer drivers"));
        }
        Ok(crate::read_drivers(&r))
    }

    pub fn add(&self, queue: &str, uri: &str, driver: &str, info: &str, location: &str) -> Result<(), String> {
        // cups-filters builds a driverless driver by asking the printer, so
        // this can take a while for a slow network printer.
        let req = crate::req_add(&self.user, queue, uri, driver, info, location);
        let r = self.send("/admin/", &req, Duration::from_secs(90))?;
        if r.ok() {
            Ok(())
        } else {
            Err(crate::explain(r.status, r.message().as_deref(), "add the printer"))
        }
    }

    pub fn delete(&self, queue: &str) -> Result<(), String> {
        self.call("/admin/", &crate::req_delete(&self.user, queue), "remove the printer").map(drop)
    }

    pub fn set_default(&self, queue: &str) -> Result<(), String> {
        self.call("/admin/", &crate::req_set_default(&self.user, queue), "make it the default printer").map(drop)
    }

    pub fn pause(&self, queue: &str) -> Result<(), String> {
        self.call("/admin/", &crate::req_pause(&self.user, queue), "pause the printer").map(drop)
    }

    pub fn resume(&self, queue: &str) -> Result<(), String> {
        self.call("/admin/", &crate::req_resume(&self.user, queue), "resume the printer").map(drop)
    }

    pub fn cancel(&self, id: i32) -> Result<(), String> {
        self.call("/jobs/", &crate::req_cancel_job(&self.user, id), "cancel the document").map(drop)
    }

    pub fn hold(&self, id: i32) -> Result<(), String> {
        self.call("/jobs/", &crate::req_hold_job(&self.user, id), "hold the document").map(drop)
    }

    pub fn release(&self, id: i32) -> Result<(), String> {
        self.call("/jobs/", &crate::req_release_job(&self.user, id), "release the document").map(drop)
    }

    /// Print CUPS' test page; Ok is the job id.
    pub fn test_page(&self, queue: &str) -> Result<i32, String> {
        let page = std::fs::read(crate::TEST_PAGE)
            .map_err(|e| format!("Could not read the test page {}: {e}", crate::TEST_PAGE))?;
        let req = crate::req_test_page(&self.user, queue, page);
        let resource = format!("/printers/{}", crate::percent_encode(queue));
        let r = self.call(&resource, &req, "print the test page")?;
        let id = r.groups(crate::ipp::group::JOB).find_map(|g| g.int("job-id")).unwrap_or(0);
        Ok(id)
    }
}

/// Split an HTTP/1.1 response into its status code and body, undoing chunked
/// transfer coding (CUPS chunks answers whose length it does not know up front,
/// such as CUPS-Get-Devices).
pub fn parse_http(raw: &[u8]) -> Result<(u16, Vec<u8>), String> {
    let split = raw
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or_else(|| "CUPS closed the connection without an answer.".to_string())?;
    let head = String::from_utf8_lossy(&raw[..split]);
    let mut body = &raw[split + 4..];
    let mut lines = head.split("\r\n");
    let status = lines.next().unwrap_or("");
    let code = status
        .split_whitespace()
        .nth(1)
        .and_then(|c| c.parse::<u16>().ok())
        .ok_or_else(|| format!("CUPS sent a malformed answer ({status:?})."))?;
    let mut chunked = false;
    let mut length: Option<usize> = None;
    for l in lines {
        if let Some((k, v)) = l.split_once(':') {
            let (k, v) = (k.trim().to_ascii_lowercase(), v.trim());
            if k == "transfer-encoding" && v.eq_ignore_ascii_case("chunked") {
                chunked = true;
            } else if k == "content-length" {
                length = v.parse().ok();
            }
        }
    }
    if !chunked {
        if let Some(n) = length {
            body = body.get(..n).ok_or("CUPS' answer was cut short.")?;
        }
        return Ok((code, body.to_vec()));
    }
    let mut out = Vec::new();
    loop {
        let eol = body
            .windows(2)
            .position(|w| w == b"\r\n")
            .ok_or("CUPS' answer was cut short.")?;
        let size_line = String::from_utf8_lossy(&body[..eol]);
        let size = usize::from_str_radix(size_line.split(';').next().unwrap_or("").trim(), 16)
            .map_err(|_| format!("CUPS sent a malformed chunk ({size_line:?})."))?;
        body = &body[eol + 2..];
        if size == 0 {
            break;
        }
        out.extend_from_slice(body.get(..size).ok_or("CUPS' answer was cut short.")?);
        body = body.get(size + 2..).unwrap_or(&[]);
    }
    Ok((code, out))
}

/// The login name of this process's uid (std has no getpwuid: read the uid
/// from /proc and look it up in /etc/passwd), else $USER.
pub fn current_user() -> String {
    let uid = std::fs::read_to_string("/proc/self/status").ok().and_then(|s| {
        s.lines()
            .find_map(|l| l.strip_prefix("Uid:"))
            .and_then(|v| v.split_whitespace().next().map(str::to_string))
    });
    let from_passwd = uid.and_then(|uid| {
        std::fs::read_to_string("/etc/passwd")
            .ok()
            .and_then(|p| user_for_uid(&p, &uid))
    });
    from_passwd
        .or_else(|| std::env::var("USER").ok())
        .or_else(|| std::env::var("LOGNAME").ok())
        .unwrap_or_else(|| "root".into())
}

pub fn user_for_uid(passwd: &str, uid: &str) -> Option<String> {
    passwd.lines().find_map(|l| {
        let f: Vec<&str> = l.split(':').collect();
        (f.len() > 2 && f[2] == uid).then(|| f[0].to_string())
    })
}
