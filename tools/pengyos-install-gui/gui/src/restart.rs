//! Restart the machine once the install is done, stick or no stick.
//!
//! The finished screen asks for the USB stick to be pulled before "Restart
//! now", and with it goes every file of the live system: `systemctl reboot`
//! failed with EIO on real hardware (the console installer), and even when it
//! runs from cache a clean shutdown needs the stick at every step. So the
//! restart comes from the kernel alone: the sysrq trigger is opened when the
//! app starts, and the button syncs, remounts everything read-only and reboots.
//! The engine has already unmounted and synced the new disk, and the live
//! session has nothing else worth saving. `systemctl reboot` is only the
//! fallback when the trigger cannot be opened. Same design as the console
//! installer's `pengyos_install_tui.install.Restart`.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::process::Command;
use std::time::Duration;

pub struct Restart {
    trigger: Option<File>,
    pause: Duration,
}

impl Restart {
    pub fn new(sysrq: &Path, pause: Duration) -> Self {
        let trigger = OpenOptions::new().write(true).open(sysrq).ok();
        Restart { trigger, pause }
    }

    /// The kernel's own trigger (the live session runs as root).
    pub fn live() -> Self {
        Self::new(Path::new("/proc/sysrq-trigger"), Duration::from_secs(1))
    }

    pub fn now(&mut self) {
        let Some(t) = self.trigger.as_mut() else {
            let _ = Command::new("systemctl").arg("reboot").spawn();
            return;
        };
        // s: sync, u: remount everything read-only, b: reboot.
        for key in [b"s", b"u"] {
            let _ = t.write_all(key);
            std::thread::sleep(self.pause);
        }
        let _ = t.write_all(b"b");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn restart_comes_from_the_kernel() {
        let dir = std::env::temp_dir().join(format!("restart-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let trigger = dir.join("sysrq-trigger");
        std::fs::write(&trigger, "").unwrap();
        Restart::new(&trigger, Duration::ZERO).now();
        assert_eq!(std::fs::read_to_string(&trigger).unwrap(), "sub");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
