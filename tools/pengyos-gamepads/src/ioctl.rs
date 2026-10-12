//! The evdev ioctl numbers and the force-feedback structs, std only.
//!
//! libc does not export the evdev ioctls, so they are built from the same
//! `_IOC` encoding the kernel header uses (uapi/linux/input.h): direction, magic
//! `'E'`, number, size. They live here, outside the `evdev` feature, so the
//! default `cargo test` checks every number against the value the kernel
//! header produces on x86_64 — the first version of this app shipped
//! `EVIOCGABS` at `0x26 + abs` and `EVIOCSFF`/`EVIOCRMFF` at `0x30`/`0x31`
//! (the real bases are `0x40`, `0x80`, `0x81`), and nothing caught it.

const IOC_NRBITS: u32 = 8;
const IOC_TYPEBITS: u32 = 8;
const IOC_SIZEBITS: u32 = 14;
const IOC_NRSHIFT: u32 = 0;
const IOC_TYPESHIFT: u32 = IOC_NRSHIFT + IOC_NRBITS;
const IOC_SIZESHIFT: u32 = IOC_TYPESHIFT + IOC_TYPEBITS;
const IOC_DIRSHIFT: u32 = IOC_SIZESHIFT + IOC_SIZEBITS;
const IOC_WRITE: u32 = 1;
const IOC_READ: u32 = 2;

const fn ioc(dir: u32, nr: u32, size: u32) -> u32 {
    (dir << IOC_DIRSHIFT) | ((b'E' as u32) << IOC_TYPESHIFT) | (nr << IOC_NRSHIFT) | (size << IOC_SIZESHIFT)
}

/// `EVIOCGABS(abs)`: `_IOR('E', 0x40 + abs, struct input_absinfo)`.
pub const fn eviocgabs(abs: u16) -> u32 {
    ioc(IOC_READ, 0x40 + abs as u32, std::mem::size_of::<AbsInfo>() as u32)
}

/// `EVIOCGBIT(ev, len)`: `_IOC(_IOC_READ, 'E', 0x20 + ev, len)`.
pub const fn eviocgbit(ev: u16, len: u32) -> u32 {
    ioc(IOC_READ, 0x20 + ev as u32, len)
}

/// `EVIOCSFF`: `_IOW('E', 0x80, struct ff_effect)` — upload an effect.
pub const EVIOCSFF: u32 = ioc(IOC_WRITE, 0x80, std::mem::size_of::<FfEffect>() as u32);
/// `EVIOCRMFF`: `_IOW('E', 0x81, int)` — erase an effect (the id is passed by value).
pub const EVIOCRMFF: u32 = ioc(IOC_WRITE, 0x81, std::mem::size_of::<i32>() as u32);

/// The bitmap length that holds every key code (`KEY_MAX` = 0x2ff), in bytes.
pub const KEY_BITS_LEN: usize = 0x2ff / 8 + 1;

/// `EV_FF`: the event type that starts and stops an uploaded effect.
pub const EV_FF: u16 = 0x15;
/// `FF_RUMBLE`: the effect type every gamepad driver with motors supports.
pub const FF_RUMBLE: u16 = 0x50;

/// `struct input_absinfo`.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default)]
pub struct AbsInfo {
    pub value: i32,
    pub minimum: i32,
    pub maximum: i32,
    pub fuzz: i32,
    pub flat: i32,
    pub resolution: i32,
}

/// `struct ff_effect`, laid out as the kernel lays it out on a 64-bit target.
///
/// `type, id, direction` (3 × u16), `struct ff_trigger` (2 × u16) and
/// `struct ff_replay` (2 × u16) make 14 bytes; the union that follows contains
/// `ff_periodic_effect`, whose `custom_data` pointer makes it 32 bytes and
/// 8-byte aligned, so it starts at offset 16 and the struct is 48 bytes. A
/// rumble effect uses the union's first four bytes:
/// `struct ff_rumble_effect { __u16 strong_magnitude; __u16 weak_magnitude; }`.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct FfEffect {
    pub type_: u16,
    /// -1 on upload asks the kernel for a free slot; it writes the slot back.
    pub id: i16,
    pub direction: u16,
    pub trigger_button: u16,
    pub trigger_interval: u16,
    /// How long one play of the effect lasts, in ms.
    pub replay_length: u16,
    pub replay_delay: u16,
    /// The union. `u64` gives it the pointer's alignment.
    pub u: [u64; 4],
}

#[cfg(target_pointer_width = "64")]
const _: () = assert!(std::mem::size_of::<FfEffect>() == 48);

impl FfEffect {
    /// A rumble effect: `strong` drives the heavy (low-frequency) motor,
    /// `weak` the light (high-frequency) one, for `length_ms`.
    pub fn rumble(strong: u16, weak: u16, length_ms: u16) -> FfEffect {
        let mut u = [0u64; 4];
        // Little-endian x86_64: the union's first u16 is the low half of u[0].
        u[0] = u64::from(strong) | (u64::from(weak) << 16);
        FfEffect {
            type_: FF_RUMBLE,
            id: -1,
            direction: 0,
            trigger_button: 0,
            trigger_interval: 0,
            replay_length: length_ms,
            replay_delay: 0,
            u,
        }
    }

    /// The (strong, weak) magnitudes this effect carries.
    pub fn rumble_magnitudes(&self) -> (u16, u16) {
        ((self.u[0] & 0xffff) as u16, ((self.u[0] >> 16) & 0xffff) as u16)
    }
}

/// `struct input_event` as written to the event node to start (`value` 1) or
/// stop (`value` 0) effect `code`. The timestamp is ignored on write.
pub fn ff_play_event(id: i16, play: bool) -> [u8; crate::EVENT_SIZE] {
    let mut buf = [0u8; crate::EVENT_SIZE];
    let tail = crate::EVENT_SIZE - 8;
    buf[tail..tail + 2].copy_from_slice(&EV_FF.to_ne_bytes());
    buf[tail + 2..tail + 4].copy_from_slice(&(id as u16).to_ne_bytes());
    buf[tail + 4..].copy_from_slice(&i32::from(play).to_ne_bytes());
    buf
}
