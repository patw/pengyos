//! Machines: the Desktop's window over `pengyos-vm`, `distrobox`, `podman` and
//! Quadlet (spec.md). Everything here is std + serde_json: what the tools print,
//! the command line each button runs, and the VNC client. The window is `app`
//! (feature `cosmic`); `backend` runs the commands.

pub mod backend;
pub mod keymap;
pub mod rfb;

#[cfg(feature = "cosmic")]
pub mod app;
#[cfg(feature = "cosmic")]
pub mod screen;

use serde_json::Value;

pub const APP_ID: &str = "io.catbee.pengyos.Machines";
pub const VM: &str = "pengyos-vm";

fn s(v: &Value, k: &str) -> String {
    v.get(k).and_then(Value::as_str).unwrap_or_default().to_string()
}

fn opt(v: &Value, k: &str) -> Option<String> {
    v.get(k).and_then(Value::as_str).filter(|x| !x.is_empty()).map(String::from)
}

// ------------------------------------------------------------------ VMs

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Snapshot {
    pub name: String,
    pub label: String,
    pub created: String,
    /// "running" or "stopped": how it was taken.
    pub taken: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Vm {
    pub name: String,
    /// running, stopped, starting, stopping, failed
    pub state: String,
    pub autostart: bool,
    pub cpus: u64,
    pub memory: String,
    pub net: String,
    pub offline: bool,
    pub ssh: Option<String>,
    pub forwards: Vec<String>,
    pub vnc_socket: String,
    pub iso: Option<String>,
    pub firmware: String,
    pub snapshots: Vec<Snapshot>,
}

impl Vm {
    pub fn from_json(v: &Value) -> Vm {
        Vm {
            name: s(v, "name"),
            state: s(v, "state"),
            autostart: v.get("autostart").and_then(Value::as_bool).unwrap_or(false),
            cpus: v.get("cpus").and_then(Value::as_u64).unwrap_or(0),
            memory: s(v, "memory"),
            net: s(v, "net"),
            offline: v.get("offline").and_then(Value::as_bool).unwrap_or(false),
            ssh: opt(v, "ssh"),
            forwards: v
                .get("forwards")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect())
                .unwrap_or_default(),
            vnc_socket: s(v, "vnc_socket"),
            iso: opt(v, "iso"),
            firmware: s(v, "firmware"),
            snapshots: v
                .get("snapshots")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .map(|x| Snapshot {
                            name: s(x, "name"),
                            label: s(x, "label"),
                            created: s(x, "created"),
                            taken: s(x, "taken"),
                        })
                        .collect()
                })
                .unwrap_or_default(),
        }
    }

    /// `pengyos-vm list --json`. Err: what to show (the tool's own words).
    pub fn parse_list(text: &str) -> Result<Vec<Vm>, String> {
        let v: Value = serde_json::from_str(text).map_err(|e| format!("pengyos-vm list: {e}"))?;
        Ok(v.as_array().map(|a| a.iter().map(Vm::from_json).collect()).unwrap_or_default())
    }

    pub fn running(&self) -> bool {
        matches!(self.state.as_str(), "running" | "starting" | "stopping")
    }

    /// Older pengyos-vm (before 0.5.0) had a TCP screen and no socket field.
    pub fn has_screen(&self) -> bool {
        self.running() && !self.vnc_socket.is_empty()
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Iso {
    pub name: String,
    pub path: String,
    pub size: String,
    pub added: String,
    pub used_by: Vec<String>,
}

/// `pengyos-vm iso list --json`.
pub fn parse_isos(text: &str) -> Result<Vec<Iso>, String> {
    let v: Value = serde_json::from_str(text).map_err(|e| format!("pengyos-vm iso list: {e}"))?;
    Ok(v.get("isos")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .map(|x| Iso {
                    name: s(x, "name"),
                    path: s(x, "path"),
                    size: s(x, "size"),
                    added: s(x, "added"),
                    used_by: x
                        .get("used_by")
                        .and_then(Value::as_array)
                        .map(|u| u.iter().filter_map(Value::as_str).map(String::from).collect())
                        .unwrap_or_default(),
                })
                .collect()
        })
        .unwrap_or_default())
}

// --------------------------------------------------- what each button runs

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|p| p.to_string()).collect()
}

#[derive(Debug, Clone, PartialEq)]
pub enum VmAction {
    Start,
    Stop,
    ForceOff,
    Autostart(bool),
    Eject,
    Delete,
    SnapshotCreate(String),
    SnapshotRevert(String),
    SnapshotDelete(String),
}

impl VmAction {
    pub fn argv(&self, vm: &str) -> Vec<String> {
        match self {
            VmAction::Start => argv(&[VM, "start", vm]),
            VmAction::Stop => argv(&[VM, "stop", vm]),
            VmAction::ForceOff => argv(&[VM, "stop", vm, "--force"]),
            VmAction::Autostart(on) => argv(&[VM, "autostart", vm, if *on { "on" } else { "off" }]),
            VmAction::Eject => argv(&[VM, "set", vm, "--eject"]),
            VmAction::Delete => argv(&[VM, "delete", vm, "--yes"]),
            VmAction::SnapshotCreate(label) => {
                let mut a = argv(&[VM, "snapshot", vm, "create"]);
                if !label.trim().is_empty() {
                    a.push(label.trim().to_string());
                }
                a
            }
            VmAction::SnapshotRevert(s) => argv(&[VM, "snapshot", vm, "revert", s, "--yes"]),
            VmAction::SnapshotDelete(s) => argv(&[VM, "snapshot", vm, "delete", s]),
        }
    }

    /// What the button is called while it works.
    pub fn doing(&self) -> &'static str {
        match self {
            VmAction::Start => "Starting",
            VmAction::Stop => "Shutting down",
            VmAction::ForceOff => "Switching off",
            VmAction::Autostart(_) => "Changing start at boot",
            VmAction::Eject => "Ejecting the ISO",
            VmAction::Delete => "Deleting",
            VmAction::SnapshotCreate(_) => "Taking a snapshot",
            VmAction::SnapshotRevert(_) => "Rolling back",
            VmAction::SnapshotDelete(_) => "Deleting the snapshot",
        }
    }
}

/// The New VM form.
#[derive(Debug, Clone, PartialEq)]
pub struct NewVm {
    pub name: String,
    /// Boot an installer ISO (a library name or a path) ...
    pub iso: String,
    /// ... or a disk image (a path or an http(s) URL).
    pub image: String,
    pub use_image: bool,
    pub cpus: String,
    pub memory: String,
    pub disk: String,
    pub bridge: bool,
    pub forwards: String,
    pub ssh_key: bool,
    pub bios: bool,
}

impl Default for NewVm {
    fn default() -> Self {
        NewVm {
            name: String::new(),
            iso: String::new(),
            image: String::new(),
            use_image: false,
            cpus: "2".into(),
            memory: "2G".into(),
            disk: "20G".into(),
            bridge: false,
            forwards: String::new(),
            ssh_key: false,
            bios: false,
        }
    }
}

pub fn valid_vm_name(n: &str) -> bool {
    let b = n.as_bytes();
    !b.is_empty()
        && b.len() <= 32
        && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit())
        && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'-')
        && n != "isos"
        && n != "images"
}

fn valid_size(t: &str) -> bool {
    let t = t.trim();
    let digits = t.trim_end_matches(['K', 'M', 'G', 'T', 'k', 'm', 'g', 't']);
    !digits.is_empty() && digits.len() + 1 >= t.len() && digits.bytes().all(|c| c.is_ascii_digit())
}

impl NewVm {
    /// The first thing wrong with the form, said as the person should fix it.
    pub fn problem(&self) -> Option<String> {
        if !valid_vm_name(self.name.trim()) {
            return Some("A name: lowercase letters, digits and dashes (like web or debian-13).".into());
        }
        if self.use_image && self.image.trim().is_empty() {
            return Some("Choose a disk image, or give its path or URL.".into());
        }
        if !self.use_image && self.iso.trim().is_empty() {
            return Some("Choose an installer ISO (add one to the library first).".into());
        }
        if !self.cpus.trim().is_empty() && self.cpus.trim().parse::<u32>().map_or(true, |n| n == 0) {
            return Some("CPUs is a number, like 2.".into());
        }
        for (what, v) in [("Memory", &self.memory), ("Disk", &self.disk)] {
            if !v.trim().is_empty() && !valid_size(v) {
                return Some(format!("{what} is a size, like 2G or 512M."));
            }
        }
        None
    }

    pub fn argv(&self) -> Vec<String> {
        let mut a = argv(&[VM, "create", self.name.trim()]);
        if self.use_image {
            a.extend(["--image".into(), self.image.trim().into()]);
        } else {
            a.extend(["--iso".into(), self.iso.trim().into()]);
        }
        for (flag, v) in [("--cpus", &self.cpus), ("--memory", &self.memory), ("--disk", &self.disk)] {
            if !v.trim().is_empty() {
                a.extend([flag.to_string(), v.trim().to_string()]);
            }
        }
        if self.bridge {
            a.extend(["--net".into(), "bridge".into()]);
        }
        for f in self.forwards.split([' ', ',']).filter(|f| !f.is_empty()) {
            a.extend(["--forward".into(), f.into()]);
        }
        if self.ssh_key {
            a.push("--ssh-key".into());
        }
        if self.bios {
            a.push("--bios".into());
        }
        a
    }
}

/// A command line as a person would type it (for "Show command").
pub fn shell_line(argv: &[String]) -> String {
    argv.iter()
        .map(|a| {
            if !a.is_empty() && a.bytes().all(|c| c.is_ascii_alphanumeric() || b"-_./:=@,+%".contains(&c)) {
                a.clone()
            } else {
                format!("'{}'", a.replace('\'', r"'\''"))
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Run `argv` in a terminal window (COSMIC Terminal): ssh, distrobox enter.
pub fn in_terminal(argv: &[String]) -> Vec<String> {
    let mut a = vec!["cosmic-term".to_string(), "--".to_string()];
    a.extend(argv.iter().cloned());
    a
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIST: &str = r#"[{"name": "deb", "state": "running", "autostart": true, "cpus": 2,
      "memory": "2G", "disk": "/home/u/vms/deb/disk.qcow2", "net": "nat", "offline": false,
      "mac": "52:54:00:aa:bb:cc", "ssh": "127.0.0.1:2201", "forwards": ["127.0.0.1:8080->80"],
      "vnc_socket": "/home/u/vms/deb/vnc.sock", "iso": null, "firmware": "uefi", "ssh_key": true,
      "snapshots": [{"name": "s1", "label": "before", "created": "2026-11-27 16:40:50", "taken": "running"}]},
      {"name": "old", "state": "stopped", "autostart": false, "cpus": 1, "memory": "1G",
      "net": "bridge", "ssh": null, "forwards": [], "vnc": "127.0.0.1:5901", "firmware": "bios"}]"#;

    #[test]
    fn parses_pengyos_vm_list() {
        let vms = Vm::parse_list(LIST).unwrap();
        assert_eq!(vms.len(), 2);
        let d = &vms[0];
        assert!(d.running() && d.has_screen() && d.autostart);
        assert_eq!(d.ssh.as_deref(), Some("127.0.0.1:2201"));
        assert_eq!(d.snapshots[0], Snapshot { name: "s1".into(), label: "before".into(), created: "2026-11-27 16:40:50".into(), taken: "running".into() });
        // An older pengyos-vm: no socket field, no snapshots.
        assert!(!vms[1].has_screen() && vms[1].snapshots.is_empty() && vms[1].iso.is_none());
        assert!(Vm::parse_list("not json").is_err());
    }

    #[test]
    fn parses_the_iso_library() {
        let t = r#"{"dir": "/home/u/vms/isos", "isos": [{"name": "debian.iso", "path": "/home/u/vms/isos/debian.iso",
            "size": "631M", "bytes": 661651456, "added": "2026-11-27 16:56", "used_by": ["deb"]}]}"#;
        let isos = parse_isos(t).unwrap();
        assert_eq!(isos[0].name, "debian.iso");
        assert_eq!(isos[0].used_by, vec!["deb"]);
    }

    #[test]
    fn actions_are_pengyos_vm_commands() {
        assert_eq!(VmAction::ForceOff.argv("deb"), ["pengyos-vm", "stop", "deb", "--force"]);
        assert_eq!(VmAction::SnapshotCreate("  before upgrade ".into()).argv("deb"),
                   ["pengyos-vm", "snapshot", "deb", "create", "before upgrade"]);
        assert_eq!(VmAction::SnapshotCreate("".into()).argv("deb"), ["pengyos-vm", "snapshot", "deb", "create"]);
        assert_eq!(VmAction::SnapshotRevert("s1".into()).argv("deb"),
                   ["pengyos-vm", "snapshot", "deb", "revert", "s1", "--yes"]);
        assert_eq!(VmAction::Delete.argv("deb"), ["pengyos-vm", "delete", "deb", "--yes"]);
    }

    #[test]
    fn new_vm_form() {
        let mut f = NewVm { name: "web".into(), iso: "debian.iso".into(), ..Default::default() };
        assert_eq!(f.problem(), None);
        assert_eq!(f.argv(), ["pengyos-vm", "create", "web", "--iso", "debian.iso", "--cpus", "2", "--memory", "2G", "--disk", "20G"]);
        f.forwards = "8080:80, 0.0.0.0:8443:443".into();
        f.ssh_key = true;
        f.bridge = true;
        let a = f.argv();
        assert!(a.ends_with(&["--net".into(), "bridge".into(), "--forward".into(), "8080:80".into(),
                              "--forward".into(), "0.0.0.0:8443:443".into(), "--ssh-key".into()]));
        f.use_image = true;
        assert!(f.problem().unwrap().contains("disk image"));
        f.image = "https://example.org/d.qcow2".into();
        assert_eq!(f.problem(), None);
        for (field, bad) in [("name", "Web"), ("name", "isos"), ("memory", "lots"), ("cpus", "0"), ("disk", "20GB")] {
            let mut g = f.clone();
            match field {
                "name" => g.name = bad.into(),
                "memory" => g.memory = bad.into(),
                "cpus" => g.cpus = bad.into(),
                _ => g.disk = bad.into(),
            }
            assert!(g.problem().is_some(), "{field}={bad}");
        }
    }

    #[test]
    fn shell_lines_quote_what_needs_it() {
        let a: Vec<String> = ["pengyos-vm", "snapshot", "deb", "create", "it's mine"].iter().map(|s| s.to_string()).collect();
        assert_eq!(shell_line(&a), r"pengyos-vm snapshot deb create 'it'\''s mine'");
    }
}
