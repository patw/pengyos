//! The evdev side of `pengyos-gamepads`: the sysfs scan, the ioctls, the event
//! reader, and the joystick-device rumble. Feature `evdev`; needs only `libc`.
//!
//! Every decision in here is a syscall and a buffer; the *meaning* of what is
//! read is decided in [`crate`] (`classify`, `PadState`, the names), so this
//! file stays thin and the tests stay display-free.

use std::fs::File;
use std::io::{Error as IoError, Read};
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::{Caps, DeviceInfo, Ev, Pad, INPUT_DIR};

// --- ioctl numbers (uapi/linux/input.h) ------------------------------------
// libc does not export the evdev ioctls, so they are built from the same
// _IOR/_IOWR macros the kernel header uses: direction, magic 'E', number, size.
const IOC_NRBITS: u32 = 8;
const IOC_TYPEBITS: u32 = 8;
const IOC_SIZEBITS: u32 = 14;
/// The direction field's width: documented here because the two direction
/// constants below are the only place it is used, and the layout is easier to
/// check against the kernel header when all four field widths are named.
#[allow(dead_code)]
const IOC_DIRBITS: u32 = 2;
const IOC_NRSHIFT: u32 = 0;
const IOC_TYPESHIFT: u32 = IOC_NRSHIFT + IOC_NRBITS;
const IOC_SIZESHIFT: u32 = IOC_TYPESHIFT + IOC_TYPEBITS;
const IOC_DIRSHIFT: u32 = IOC_SIZESHIFT + IOC_SIZEBITS;
const IOC_READ: u32 = 1;
const IOC_WRITE: u32 = 2;

const fn ioc(dir: u32, nr: u32, size: u32) -> u32 {
    (dir << IOC_DIRSHIFT) | (b'E' as u32) << IOC_TYPESHIFT | nr << IOC_NRSHIFT | size << IOC_SIZESHIFT
}

pub const EVIOCGVERSION: u32 = ioc(IOC_READ, 0x01, 4);
pub const EVIOCGID: u32 = ioc(IOC_READ, 0x02, 8);
pub const EVIOCGNAME: u32 = ioc(IOC_READ, 0x06, 64);
pub const EVIOCGPHYS: u32 = ioc(IOC_READ, 0x07, 128);
pub const EVIOCGUNIQ: u32 = ioc(IOC_READ, 0x08, 128);
pub const EVIOCGPROP: u32 = ioc(IOC_READ, 0x09, 128);
pub const EVIOCGABS: fn(u16) -> u32 = |n| ioc(IOC_READ, u32::from(n) + 0x26, 24);
pub const EVIOCGBIT: fn(u16) -> u32 = |n| ioc(IOC_READ, u32::from(n) + 0x20, 64);
pub const EVIOCSFF: u32 = ioc(IOC_WRITE, 0x30, std::mem::size_of::<FfEffect>() as u32);
pub const EVIOCRMFF: u32 = ioc(IOC_WRITE, 0x31, 4);

/// `struct input_absinfo`.
#[repr(C)]
#[derive(Clone, Copy, Default)]
pub struct AbsRaw {
    pub value: i32,
    pub minimum: i32,
    pub maximum: i32,
    pub fuzz: i32,
    pub flat: i32,
    pub resolution: i32,
}

/// `struct input_ff_effect` (uapi/linux/input.h), the layout ff-memset accepts.
///
/// The anonymous union in the header is flattened: `small` and `large` are the
/// rumble pair at the same offset as the `direction`/`gain`/`period` triple,
/// which is exactly what a rumble effect writes.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct FfEffect {
    pub type_: u16,
    pub id: i16,
    pub direction: u16,
    pub gain: u16,
    pub period: u16,
    pub magnitude: u16,
    pub delay: u16,
    pub duration: u16,
    pub attack_length: u16,
    pub attack_level: u16,
    pub fade_length: u16,
    pub fade_level: u16,
    pub repeat_length: u16,
    pub repeat_level: u16,
}

impl Default for FfEffect {
    fn default() -> Self {
        FfEffect {
            type_: 0x50, // FF_RUMBLE
            id: -1,      // let the kernel pick a slot
            direction: 0,
            gain: 0,
            period: 0,
            magnitude: 0,
            delay: 0,
            duration: 0,
            attack_length: 0,
            attack_level: 0,
            fade_length: 0,
            fade_level: 0,
            repeat_length: 0,
            repeat_level: 0,
        }
    }
}

const _: () = assert!(std::mem::size_of::<FfEffect>() == 28);

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
    Ok(crate::dedup_pads(infos.into_iter().map(|i| i.pad()).collect()))
}

/// `name`, `phys`, `modalias` and the capability groups for one event node.
fn read_sysfs(node: &str, file_name: &str) -> Option<DeviceInfo> {
    let device = Path::new("/sys/class/input").join(file_name).join("device");
    if !device.exists() {
        return None;
    }
    let sys = sys_name(&device)?;
    let name = read_trimmed(&device.join("name"));
    let phys = read_trimmed(&device.join("phys"));
    let modalias = read_trimmed(&device.join("modalias"));
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
        let mut raw = AbsRaw::default();
        let rc = unsafe {
            libc::ioctl(
                self.file.as_raw_fd(),
                EVIOCGABS(code) as libc::c_ulong,
                &mut raw as *mut AbsRaw,
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
        let mut bytes = [0u8; 64];
        let rc = unsafe {
            libc::ioctl(
                self.file.as_raw_fd(),
                EVIOCGBIT(kind) as libc::c_ulong,
                bytes.as_mut_ptr(),
            )
        };
        let mut set = std::collections::BTreeSet::new();
        if rc == 0 {
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

/// The joystick node that belongs to a pad, from the sysfs parent directory:
/// `/sys/class/input/event13/device` and `/sys/class/input/js0/device` are the
/// same device when the pad has a `/dev/input/js0`.
pub fn js_device(pad: &Pad) -> Option<String> {
    let event_device = std::fs::canonicalize(format!("/sys/class/input/{}/device", pad.sys)).ok()?;
    let js_root = Path::new("/sys/class/input");
    let entries = std::fs::read_dir(js_root).ok()?;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !name.starts_with("js") {
            continue;
        }
        let same = std::fs::canonicalize(js_root.join(&name).join("device"))
            .ok()
            .is_some_and(|p| p == event_device);
        if same {
            return Some(format!("/dev/input/{name}"));
        }
    }
    None
}

/// Ask a pad to rumble for `for_` long, over its `/dev/input/jsN` node.
///
/// Needs the `joydev` module (the image loads it at boot) and `CONFIG_INPUT_FF_MEMSET`
/// (the kernel's force-feedback layer). The node is group `input`, mode 0660 —
/// the desktop user is in that group. Every failure is returned as the sentence
/// the app shows, because a pad without motors answers `ENOTSUP`.
pub fn rumble(pad: &Pad, weak: u16, strong: u16, for_: Duration) -> Result<(), String> {
    let path = js_device(pad)
        .ok_or_else(|| format!("{} has no joystick (/dev/input/js) node", pad.name))?;
    let raw = unsafe {
        libc::open(
            PathBuf::from(&path).to_string_lossy().as_ptr() as *const libc::c_char,
            libc::O_RDWR | libc::O_CLOEXEC,
        )
    };
    if raw < 0 {
        let e = IoError::last_os_error();
        return Err(format!("{path}: {e}"));
    }
    let file = unsafe { File::from_raw_fd(raw) };
    let effect = FfEffect {
        type_: 0x50,
        id: -1,
        direction: 0,
        gain: ((weak as u32 + strong as u32) / 2) as u16,
        period: 0,
        magnitude: 0,
        delay: 0,
        duration: (for_.as_millis() as u32).clamp(0, u32::from(u16::MAX)) as u16,
        attack_length: 0,
        attack_level: weak,
        fade_length: 0,
        fade_level: 0,
        repeat_length: 0,
        repeat_level: strong,
    };
    let rc = unsafe {
        libc::ioctl(
            file.as_raw_fd(),
            EVIOCSFF as libc::c_ulong,
            &effect as *const FfEffect,
        )
    };
    if rc != 0 {
        let e = IoError::last_os_error();
        return Err(format!("{path}: {e}"));
    }
    std::thread::sleep(for_);
    // Stop: remove the effect so the motor does not keep buzzing.
    let id = effect.id;
    let _ = unsafe {
        libc::ioctl(
            file.as_raw_fd(),
            EVIOCRMFF as libc::c_ulong,
            &id as *const i16,
        )
    };
    Ok(())
}
