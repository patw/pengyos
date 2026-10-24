//! The evdev side of `pengyos-gamepads`: the sysfs scan, the ioctls, the event
//! reader, and the joystick-device rumble. Feature `evdev`; needs only `libc`.
//!
//! Every decision in here is a syscall and a buffer; the *meaning* of what is
//! read is decided in [`crate`] (`classify`, `PadState`, the names), so this
//! file stays thin and the tests stay display-free.

use std::fs::File;
use std::io::{Error as IoError, Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::path::Path;
use std::time::Duration;

use crate::{Caps, DeviceInfo, Ev, Pad, INPUT_DIR};

use crate::ioctl::{self, AbsInfo, FfEffect};

// --- enumeration -----------------------------------------------------------

/// Every input device the system exposes, classified.
///
/// Enumeration reads **sysfs**, not the device nodes: `/sys/class/input/*/device`
/// is world-readable, so the list is complete even for a user who may not open
/// the node (the app then says so on the card instead of showing nothing).
pub fn pads() -> Result<Vec<Pad>, String> {
    let dir = Path::new(INPUT_DIR);
    let mut infos = Vec::new();
    let entries = std::fs::read_dir(dir)
        .map_err(|e| format!("cannot read {INPUT_DIR}: {e}"))?;
    for entry in entries.flatten() {
        let file_name = entry.file_name().to_string_lossy().to_string();
        if !file_name.starts_with("event") {
            continue;
        }
        let path = entry.path().to_string_lossy().to_string();
        let Some(info) = read_sysfs(&path, &file_name) else { continue };
        infos.push(info);
    }
    infos.sort_by(|a, b| sysfs_number(&a.sys).cmp(&sysfs_number(&b.sys)).then(a.sys.cmp(&b.sys)));
    let pads = crate::dedup_pads(infos.into_iter().map(|i| i.pad()).collect());
    Ok(crate::number_same_names(pads))
}

/// `name`, `phys`, `modalias`, `uniq` and the capability groups for one event node.
fn read_sysfs(node: &str, file_name: &str) -> Option<DeviceInfo> {
    let device = Path::new("/sys/class/input").join(file_name).join("device");
    if !device.exists() {
        return None;
    }
    let sys = sys_name(&device)?;
    let name = read_trimmed(&device.join("name"));
    let phys = read_trimmed(&device.join("phys"));
    let modalias = read_trimmed(&device.join("modalias"));
    let uniq = read_trimmed(&device.join("uniq"));
    let mut caps_text = String::new();
    for group in ["ev", "key", "rel", "abs", "msc", "sw", "led", "ff"] {
        let words = read_trimmed(&device.join("capabilities").join(group));
        caps_text.push_str(&format!("{group} {words}\n"));
    }
    Some(DeviceInfo {
        path: node.to_string(),
        sys,
        name,
        phys,
        modalias,
        uniq,
        caps: crate::caps_from_sysfs(&caps_text),
    })
}

/// `/sys/devices/…/input13` -> 13; a name with no trailing number sorts last.
fn sysfs_number(sys: &str) -> u64 {
    sys
        .trim_start_matches(|c: char| !c.is_ascii_digit())
        .parse::<u64>()
        .unwrap_or(u64::MAX)
}

/// `/sys/devices/pci0000:00/…/input13` -> `input13`.
fn sys_name(device: &Path) -> Option<String> {
    std::fs::canonicalize(device)
        .ok()
        .and_then(|p| p.file_name().map(|s| s.to_string_lossy().into_owned()))
}

fn read_trimmed(path: &Path) -> String {
    std::fs::read_to_string(path)
        .map(|s| s.trim().to_string())
        .unwrap_or_default()
}

// --- the event reader ------------------------------------------------------

/// A device opened for reading.
pub struct Device {
    file: File,
    pub path: String,
}

impl Device {
    /// Open a node. The error text is what the card shows: a user not in the
    /// `input` group gets `Permission denied`, and the app turns that into the
    /// sentence that explains the group.
    pub fn open(pad: &Pad) -> Result<Device, String> {
        let file = File::open(&pad.path)
            .map_err(|e| format!("{}: {e}", pad.path))?;
        Ok(Device { file, path: pad.path.clone() })
    }

    /// Wrap any readable descriptor (a pipe, a file of recorded events) as a
    /// device. The reader is the same code, which is what lets the tests drive
    /// it with synthetic events.
    pub fn from_file(file: File, path: impl Into<String>) -> Device {
        Device { file, path: path.into() }
    }

    /// `EVIOCGABS` for one axis: the range the panel scales the bar by.
    pub fn axis_info(&self, code: u16) -> crate::AxisInfo {
        let mut raw = AbsInfo::default();
        let rc = unsafe {
            libc::ioctl(
                self.file.as_raw_fd(),
                ioctl::eviocgabs(code) as libc::c_ulong,
                &mut raw as *mut AbsInfo,
            )
        };
        if rc != 0 {
            return crate::AxisInfo::default();
        }
        crate::AxisInfo {
            value: raw.value,
            min: raw.minimum,
            max: raw.maximum,
            flat: raw.flat,
            fuzz: raw.fuzz,
        }
    }

    /// `EVIOCGBIT` for one event type, as a code set.
    pub fn bits(&self, kind: u16) -> Caps {
        let mut bytes = [0u8; ioctl::KEY_BITS_LEN];
        let rc = unsafe {
            libc::ioctl(
                self.file.as_raw_fd(),
                ioctl::eviocgbit(kind, ioctl::KEY_BITS_LEN as u32) as libc::c_ulong,
                bytes.as_mut_ptr(),
            )
        };
        let mut set = std::collections::BTreeSet::new();
        // EVIOCGBIT answers the number of bytes it copied, not 0.
        if rc >= 0 {
            for (i, byte) in bytes.iter().enumerate() {
                for bit in 0..8u32 {
                    if byte & (1 << bit) != 0 {
                        set.insert((i * 8 + bit as usize) as u16);
                    }
                }
            }
        }
        let mut caps = Caps::default();
        match kind {
            crate::EV_KEY => caps.keys = set,
            crate::EV_REL => caps.rel = set,
            crate::EV_ABS => caps.abs = set,
            crate::EV_SW => caps.sw = set,
            _ => {}
        }
        caps
    }

    /// Read the events queued on the device, waiting at most `timeout`.
    ///
    /// An empty `Vec` means "nothing happened", not an error. `Err` is the
    /// device going away (`ENODEV`, the usual one: it was unplugged) or a
    /// descriptor that cannot be read.
    pub fn read(&mut self, timeout: Duration) -> Result<Vec<Ev>, String> {
        let fd = self.file.as_raw_fd();
        if !poll_readable(fd, timeout)? {
            return Ok(Vec::new());
        }
        let mut buf = [0u8; EVENT_BUF];
        match self.file.read(&mut buf) {
            Ok(0) => Ok(Vec::new()),
            Ok(n) => Ok(buf[..n].chunks(crate::EVENT_SIZE).filter_map(crate::decode_event).collect()),
            Err(e) if e.raw_os_error() == Some(libc::EAGAIN) => Ok(Vec::new()),
            Err(e) if e.raw_os_error() == Some(libc::ENODEV) => {
                Err(format!("{} is gone ({e})", self.path))
            }
            Err(e) => Err(format!("{}: {e}", self.path)),
        }
    }
}

const EVENT_BUF: usize = crate::EVENT_SIZE * 64;

/// Block until the descriptor is readable, or the timeout passes.
fn poll_readable(fd: RawFd, timeout: Duration) -> Result<bool, String> {
    let mut fds: libc::fd_set = unsafe { std::mem::zeroed() };
    unsafe {
        libc::FD_ZERO(&mut fds);
        if fd >= libc::FD_SETSIZE as libc::c_int {
            return Err("device descriptor is out of range for select".into());
        }
        libc::FD_SET(fd, &mut fds);
    }
    let mut tv = libc::timeval {
        tv_sec: timeout.as_secs() as libc::time_t,
        tv_usec: timeout.subsec_micros() as libc::suseconds_t,
    };
    let rc = unsafe { libc::select(fd + 1, &mut fds, std::ptr::null_mut(), std::ptr::null_mut(), &mut tv) };
    if rc < 0 {
        let e = IoError::last_os_error();
        if e.raw_os_error() == Some(libc::EINTR) {
            return Ok(false);
        }
        return Err(format!("select: {e}"));
    }
    Ok(rc > 0)
}

// --- rumble ---------------------------------------------------------------

/// Rumble a pad for `for_`, through its **event** node.
///
/// Force feedback is an evdev interface: the effect is uploaded with
/// `EVIOCSFF`, started by writing an `EV_FF` event with the effect's id, and
/// erased with `EVIOCRMFF` (which also stops it). The joystick node
/// (`/dev/input/jsN`) implements none of this — that is where the first
/// version sent it, and why it answered `ENOTTY`. The event node is group
/// `input`, mode 0660, so opening it read-write needs the same group the live
/// panel already needs to read it. A pad without motors answers `EINVAL`.
pub fn rumble(pad: &Pad, strong: u16, weak: u16, for_: Duration) -> Result<(), String> {
    let path = &pad.path;
    let mut file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(|e| format!("{path}: {e}"))?;
    let length = for_.as_millis().min(u128::from(u16::MAX)) as u16;
    let mut effect = FfEffect::rumble(strong, weak, length);
    // The kernel writes the slot it chose back into `effect.id`.
    let rc = unsafe {
        libc::ioctl(file.as_raw_fd(), ioctl::EVIOCSFF as libc::c_ulong, &mut effect as *mut FfEffect)
    };
    if rc < 0 {
        let e = IoError::last_os_error();
        return Err(match e.raw_os_error() {
            Some(libc::EINVAL) | Some(libc::ENOSYS) | Some(libc::ENOTTY) => {
                format!("{} has no rumble motors the kernel can drive ({e})", pad.name)
            }
            _ => format!("{path}: {e}"),
        });
    }
    let id = effect.id;
    let played = file.write_all(&ioctl::ff_play_event(id, true));
    if played.is_ok() {
        std::thread::sleep(for_);
        let _ = file.write_all(&ioctl::ff_play_event(id, false));
    }
    // Erase the effect: frees the slot and stops the motors if still running.
    let _ = unsafe {
        libc::ioctl(file.as_raw_fd(), ioctl::EVIOCRMFF as libc::c_ulong, libc::c_int::from(id))
    };
    played.map_err(|e| format!("{path}: starting the effect: {e}"))
}
