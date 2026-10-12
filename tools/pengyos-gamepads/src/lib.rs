//! `pengyos-gamepads` — the decision logic of the "Gamepads" app.
//!
//! Everything here is **std only**: the device model built from a sysfs
//! snapshot, which devices are game controllers and which are a keyboard, an
//! audio jack or a remote, the axis/button/hat names Linux's event codes map to,
//! the event decoder, and the live button/axis state the test panel draws. No
//! display, no D-Bus, no /dev — so `cargo test` covers every decision the app
//! makes. The syscalls live in [`raw`] (feature `evdev`) and the window in
//! [`app`] (feature `cosmic`), exactly like `pengyos-disks`.

use std::collections::BTreeSet;

/// The app id, the `.desktop` file's name and the window's WM_CLASS.
pub const APP_ID: &str = "io.catbee.pengyos.Gamepads";

/// The directory evdev devices appear in.
pub const INPUT_DIR: &str = "/dev/input";

// --- Linux input event codes (uapi/linux/input-event-codes.h) --------------
// Named by hand: the image's kernel headers are not a build dependency of a
// userspace crate, and a hand-checked list is greppable in tests.
pub const EV_KEY: u16 = 1;
pub const EV_REL: u16 = 2;
pub const EV_ABS: u16 = 3;
pub const EV_MSC: u16 = 4;
pub const EV_SW: u16 = 5;

pub const ABS_X: u16 = 0x00;
pub const ABS_Y: u16 = 0x01;
pub const ABS_Z: u16 = 0x02;
pub const ABS_RX: u16 = 0x03;
pub const ABS_RY: u16 = 0x04;
pub const ABS_RZ: u16 = 0x05;
pub const ABS_THROTTLE: u16 = 0x06;
pub const ABS_RUDDER: u16 = 0x07;
pub const ABS_WHEEL: u16 = 0x08;
pub const ABS_GAS: u16 = 0x09;
pub const ABS_BRAKE: u16 = 0x0a;
pub const ABS_AIRBRAKE: u16 = 0x0b;
pub const ABS_STEER: u16 = 0x28;
pub const ABS_HAT0X: u16 = 0x22;
pub const ABS_HAT0Y: u16 = 0x23;
pub const ABS_HAT1X: u16 = 0x24;
pub const ABS_HAT1Y: u16 = 0x25;
pub const ABS_HAT2X: u16 = 0x26;
pub const ABS_HAT2Y: u16 = 0x27;
pub const ABS_MT_POSITION_X: u16 = 0x35;
pub const ABS_MT_POSITION_Y: u16 = 0x36;

pub const REL_X: u16 = 0x00;
pub const REL_Y: u16 = 0x01;
pub const REL_WHEEL: u16 = 0x08;

pub const ABS_MT_PRESSURE: u16 = 0x39;

pub const BTN_MISC: u16 = 0x100;
pub const BTN_A: u16 = 0x130;
pub const BTN_B: u16 = 0x131;
pub const BTN_C: u16 = 0x132;
pub const BTN_X: u16 = 0x133;
pub const BTN_Y: u16 = 0x134;
pub const BTN_Z: u16 = 0x135;
pub const BTN_TL: u16 = 0x136;
pub const BTN_TR: u16 = 0x137;
pub const BTN_TL2: u16 = 0x138;
pub const BTN_TR2: u16 = 0x139;
pub const BTN_SELECT: u16 = 0x13a;
pub const BTN_START: u16 = 0x13b;
pub const BTN_MODE: u16 = 0x13c;
pub const BTN_THUMBL: u16 = 0x13d;
pub const BTN_THUMBR: u16 = 0x13e;
pub const BTN_TRIGGER: u16 = 0x145;
pub const BTN_THUMB: u16 = 0x146;
pub const BTN_BASE: u16 = 0x147;
pub const BTN_DEAD: u16 = 0x148;
pub const BTN_MOUSE: u16 = 0x110;
pub const BTN_RIGHT: u16 = 0x111;
pub const BTN_MIDDLE: u16 = 0x112;
pub const BTN_SIDE: u16 = 0x113;
pub const BTN_EXTRA: u16 = 0x114;
pub const BTN_TOOL_FINGER: u16 = 0x141;
pub const BTN_DIGI: u16 = 0x140;
pub const BTN_TOUCH: u16 = 0x140;
pub const BTN_PINKIE: u16 = 0x149;
// Keys below BTN_MISC: what a keyboard, a laptop hotkey device and a media
// remote report. (KEY_MENU shares BTN_START's value, so it is not listed.)
pub const KEY_A: u16 = 0x1e;
pub const KEY_ESC: u16 = 0x01;
pub const KEY_VOLUMEUP: u16 = 0x19;
pub const KEY_POWER: u16 = 0x74;
pub const KEY_ENTER: u16 = 0x1c;

/// The buttons a gamepad test panel labels, in the order they are drawn.
pub const GAME_BUTTONS: [u16; 15] = [
    BTN_A, BTN_B, BTN_C, BTN_X, BTN_Y, BTN_Z, BTN_TL, BTN_TR, BTN_TL2, BTN_TR2, BTN_SELECT,
    BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR,
];

/// Absolute axes a test panel shows, in the order they are drawn.
pub const GAME_AXES: [u16; 14] = [
    ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_Z, ABS_RZ, ABS_THROTTLE, ABS_RUDDER, ABS_GAS, ABS_BRAKE,
    ABS_WHEEL, ABS_STEER, ABS_HAT0X, ABS_HAT0Y,
];

// --- capability set --------------------------------------------------------

/// What a device can report, as event types and their codes.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Caps {
    pub ev: BTreeSet<u16>,
    pub keys: BTreeSet<u16>,
    pub rel: BTreeSet<u16>,
    pub abs: BTreeSet<u16>,
    pub msc: BTreeSet<u16>,
    pub sw: BTreeSet<u16>,
    pub led: BTreeSet<u16>,
    pub ff: BTreeSet<u16>,
}

impl Caps {
    pub fn has_key(&self, code: u16) -> bool {
        self.ev.contains(&EV_KEY) && self.keys.contains(&code)
    }
    pub fn has_abs(&self, code: u16) -> bool {
        self.ev.contains(&EV_ABS) && self.abs.contains(&code)
    }
    pub fn has_rel(&self, code: u16) -> bool {
        self.ev.contains(&EV_REL) && self.rel.contains(&code)
    }

    /// The D-pad: Linux reports a hat as a pair of signed absolute axes.
    pub fn hats(&self) -> Vec<(u16, u16)> {
        [
            (ABS_HAT0X, ABS_HAT0Y),
            (ABS_HAT1X, ABS_HAT1Y),
            (ABS_HAT2X, ABS_HAT2Y),
        ]
        .into_iter()
        .filter(|(x, y)| self.has_abs(*x) && self.has_abs(*y))
        .collect()
    }

    /// One line per capability group, for the device card.
    pub fn summary(&self) -> String {
        let mut bits: Vec<String> = Vec::new();
        let sticks = self.count_sticks();
        if sticks > 0 {
            bits.push(format!("{} stick{}", sticks, if sticks == 1 { "" } else { "s" }));
        }
        let buttons = self.game_buttons().len();
        if buttons > 0 {
            bits.push(format!("{buttons} buttons"));
        }
        if self.hats().len() > 0 {
            bits.push(format!("{} D-pad", self.hats().len()));
        }
        let triggers = [ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE, ABS_THROTTLE, ABS_RUDDER]
            .iter()
            .filter(|a| self.has_abs(**a))
            .count();
        if triggers > 0 {
            bits.push(format!("{triggers} trigger/throttle axes"));
        }
        if self.has_abs(ABS_STEER) {
            bits.push("steering".into());
        }
        if self.has_rel(REL_X) && self.has_rel(REL_Y) {
            bits.push("relative motion".into());
        }
        if self.ev.contains(&EV_SW) && !self.sw.is_empty() {
            bits.push(format!("{} switches", self.sw.len()));
        }
        if self.ff.contains(&0x50) {
            bits.push("rumble".into());
        }
        if bits.is_empty() {
            "no axes or buttons".into()
        } else {
            bits.join(", ")
        }
    }

    /// Analog sticks: a pair of X/Y axes that is not a touch screen.
    fn count_sticks(&self) -> usize {
        let mut n = 0;
        if self.has_abs(ABS_X) && self.has_abs(ABS_Y) {
            n += 1;
        }
        if self.has_abs(ABS_RX) && self.has_abs(ABS_RY) {
            n += 1;
        }
        n
    }

    pub fn game_buttons(&self) -> Vec<u16> {
        GAME_BUTTONS.iter().copied().filter(|b| self.has_key(*b)).collect()
    }

    /// The axes the test panel draws: the game axes present, plus any other
    /// absolute axis the device reports (so nothing a pad has is hidden).
    pub fn shown_axes(&self) -> Vec<u16> {
        let mut out: Vec<u16> = GAME_AXES.iter().copied().filter(|a| self.has_abs(*a)).collect();
        for a in &self.abs {
            if !out.contains(a) && !is_multitouch(*a) {
                out.push(*a);
            }
        }
        out
    }
}

fn is_multitouch(code: u16) -> bool {
    (0x30..=0x3f).contains(&code)
}

// --- names ------------------------------------------------------------------

/// The label a test panel puts on a button code.
pub fn button_name(code: u16) -> &'static str {
    match code {
        BTN_A => "A",
        BTN_B => "B",
        BTN_C => "C",
        BTN_X => "X",
        BTN_Y => "Y",
        BTN_Z => "Z",
        BTN_TL => "Left bumper",
        BTN_TR => "Right bumper",
        BTN_TL2 => "Left bumper 2",
        BTN_TR2 => "Right bumper 2",
        BTN_SELECT => "Select",
        BTN_START => "Start",
        BTN_MODE => "Guide",
        BTN_THUMBL => "Left stick in",
        BTN_THUMBR => "Right stick in",
        BTN_TRIGGER => "Trigger",
        BTN_THUMB => "Thumb",
        BTN_BASE => "Base",
        BTN_DEAD => "Dead",
        BTN_PINKIE => "Pinkie",
        BTN_MOUSE => "Mouse button",
        BTN_DIGI => "Pen tip",
        _ => "Button",
    }
}

/// The label a test panel puts on an absolute axis code.
pub fn axis_name(code: u16) -> &'static str {
    match code {
        ABS_X => "Left stick X",
        ABS_Y => "Left stick Y",
        ABS_RX => "Right stick X",
        ABS_RY => "Right stick Y",
        ABS_Z => "Left trigger",
        ABS_RZ => "Right trigger",
        ABS_THROTTLE => "Throttle",
        ABS_RUDDER => "Rudder",
        ABS_GAS => "Accelerator",
        ABS_BRAKE => "Brake",
        ABS_AIRBRAKE => "Airbrake",
        ABS_WHEEL => "Wheel",
        ABS_STEER => "Steering",
        ABS_HAT0X => "D-pad left/right",
        ABS_HAT0Y => "D-pad up/down",
        ABS_HAT1X => "Hat 1 left/right",
        ABS_HAT1Y => "Hat 1 up/down",
        ABS_HAT2X => "Hat 2 left/right",
        ABS_HAT2Y => "Hat 2 up/down",
        ABS_MT_POSITION_X => "Touch X",
        ABS_MT_POSITION_Y => "Touch Y",
        _ => "Axis",
    }
}

fn rel_name(code: u16) -> &'static str {
    match code {
        REL_X => "X",
        REL_Y => "Y",
        REL_WHEEL => "Wheel",
        _ => "axis",
    }
}

fn key_name(code: u16) -> String {
    // The names a remote / keypad test panel needs; anything else is a code.
    match code {
        KEY_ESC => "Esc",
        KEY_ENTER => "Enter",
        KEY_A => "A",
        KEY_VOLUMEUP => "Vol +",
        KEY_POWER => "Power",
        _ => return format!("{code:#04x}"),
    }
    .to_string()
}

/// The short label a key gets in the panel (a remote, a keypad, a mouse).
pub fn key_label(code: u16) -> String {
    key_name(code)
}

// --- classification --------------------------------------------------------

/// What kind of device this is, decided from its capabilities.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Gamepad,
    Joystick,
    Flight,
    Throttle,
    Wheel,
    Pedals,
    Touch,
    Mouse,
    Keyboard,
    Remote,
    Keypad,
    Audio,
    Other,
}

impl Kind {
    pub fn title(&self) -> &'static str {
        match self {
            Kind::Gamepad => "Gamepad",
            Kind::Joystick => "Joystick",
            Kind::Flight => "Flight stick",
            Kind::Throttle => "Throttle quadrant",
            Kind::Wheel => "Steering wheel",
            Kind::Pedals => "Pedals",
            Kind::Touch => "Touch screen",
            Kind::Mouse => "Mouse",
            Kind::Keyboard => "Keyboard",
            Kind::Remote => "Remote",
            Kind::Keypad => "Keypad",
            Kind::Audio => "Audio jack",
            Kind::Other => "Input device",
        }
    }

    /// The icon name the card and the list use (freedesktop icon names; the
    /// Cosmic theme ships all of them).
    pub fn icon(&self) -> &'static str {
        match self {
            Kind::Gamepad | Kind::Joystick | Kind::Flight | Kind::Throttle | Kind::Wheel
            | Kind::Pedals => "input-gaming",
            Kind::Mouse => "input-mouse",
            Kind::Keyboard => "input-keyboard",
            Kind::Touch => "input-tablet",
            // No input-remote icon in the Cosmic theme (measured 26.10.24);
            // a remote is a keyboard-shaped device, so that is its picture.
            Kind::Remote => "input-keyboard",
            Kind::Audio => "audio-headphones",
            _ => "input-keyboard",
        }
    }

    /// Listed as a controller: the app's headline list.
    pub fn is_controller(&self) -> bool {
        matches!(
            self,
            Kind::Gamepad | Kind::Joystick | Kind::Flight | Kind::Throttle | Kind::Wheel | Kind::Pedals
        )
    }
}

/// Decide what a device is. Order matters: a mouse answers ABS_X/ABS_Y +
// REL_X/REL_Y, a touch screen answers multitouch axes, and a wheel answers
// ABS_STEER, so the specific shapes are matched before the general ones.
pub fn classify(caps: &Caps) -> Kind {
    let game_buttons = caps.game_buttons().len();
    let sticks = caps.count_sticks();
    let has_game_key = caps.has_key(BTN_A)
        || caps.has_key(BTN_B)
        || caps.has_key(BTN_X)
        || caps.has_key(BTN_Y)
        || caps.has_key(BTN_TL)
        || caps.has_key(BTN_TR)
        || caps.has_key(BTN_START)
        || caps.has_key(BTN_SELECT)
        || caps.has_key(BTN_MODE)
        || caps.has_key(BTN_TRIGGER);

    if caps.has_abs(ABS_STEER) && (caps.has_abs(ABS_GAS) || caps.has_abs(ABS_BRAKE)) {
        return Kind::Wheel;
    }
    if (caps.has_abs(ABS_GAS) || caps.has_abs(ABS_BRAKE)) && !caps.has_abs(ABS_STEER) {
        return Kind::Pedals;
    }
    if caps.has_abs(ABS_THROTTLE) || caps.has_abs(ABS_RUDDER) {
        return Kind::Throttle;
    }
    if sticks >= 1 && game_buttons >= 3 && has_game_key {
        return Kind::Gamepad;
    }
    if caps.has_abs(ABS_X) && caps.has_abs(ABS_Y) && game_buttons >= 1 {
        return Kind::Joystick;
    }
    if sticks >= 1 && !caps.hats().is_empty() {
        return Kind::Flight;
    }
    if caps.has_abs(ABS_MT_POSITION_X) && caps.has_abs(ABS_MT_POSITION_Y) {
        return Kind::Touch;
    }
    if caps.has_rel(REL_X) && caps.has_rel(REL_Y) && caps.has_key(BTN_MOUSE) {
        return Kind::Mouse;
    }
    if caps.has_key(KEY_A) && caps.keys.len() >= 15 {
        return Kind::Keyboard;
    }
    if caps.has_key(KEY_A) {
        return Kind::Keypad;
    }
    if caps.ev.contains(&EV_SW) && caps.keys.is_empty() && caps.abs.is_empty() && caps.rel.is_empty() {
        // Switches only: a headphone jack reporting its insertion sensor.
        return Kind::Audio;
    }
    if caps.ev.contains(&EV_SW) && !caps.ev.contains(&EV_ABS) && !caps.ev.contains(&EV_REL) {
        return Kind::Remote;
    }
    // Only button-shaped keys (BTN_MISC and above), no axes, no relative
    // motion: a media remote, an audio-jack switch panel, a numpad. Three of
    // them is a remote you can drive; one or two is a lone button.
    if caps.keys.iter().all(|k| *k >= BTN_MISC) && !caps.keys.is_empty() {
        return if caps.keys.len() >= 3 { Kind::Remote } else { Kind::Keypad };
    }
    // Audio jacks report only KEY_* >= 0x160 (SND_KEY_*), which is a key-remote
    // shape: they land in Kind::Remote, and the icon mapping draws them a
    // headphone. There is no separate classification to keep in sync.
    if caps.keys.is_empty() && caps.abs.is_empty() && caps.rel.is_empty() && caps.sw.is_empty() {
        return Kind::Other;
    }
    Kind::Other
}

// --- the device model ------------------------------------------------------

/// A device's fields, read from sysfs (`/sys/class/input/<dev>/device/`) or
/// from the evdev ioctls. Pure data: [`DeviceInfo::pad`] does the deciding.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct DeviceInfo {
    /// The node to open, e.g. `/dev/input/event13`.
    pub path: String,
    /// The sysfs class directory, e.g. `input13`.
    pub sys: String,
    pub name: String,
    /// `device/phys`, e.g. `usb-0000:00:14.0-3/input0`.
    pub phys: String,
    /// `device/modalias`, e.g. `usb:v045Ep028Ed0700dc00dsc00p00ic01isc02ip00in01`.
    pub modalias: String,
    pub caps: Caps,
}

impl DeviceInfo {
    /// The pad the app shows for this device.
    pub fn pad(&self) -> Pad {
        let kind = classify(&self.caps);
        Pad {
            path: self.path.clone(),
            sys: self.sys.clone(),
            name: if self.name.trim().is_empty() {
                self.sys.clone()
            } else {
                self.name.trim().to_string()
            },
            phys: self.phys.trim().to_string(),
            modalias: self.modalias.trim().to_string(),
            caps: self.caps.clone(),
            kind,
        }
    }
}

/// A game controller (or any input device) the app can show and test.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Pad {
    pub path: String,
    pub sys: String,
    pub name: String,
    pub phys: String,
    pub modalias: String,
    pub caps: Caps,
    pub kind: Kind,
}

impl Pad {
    /// `usb:v045Ep028Ed0700…` -> `(Some("1118"), Some("654"))`.
    ///
    /// USB and HID modalias carry the ids as four hex digits after `v` and `p`;
    /// a platform device (a laptop's AT keyboard, WMI hotkeys) carries none and
    /// answers `(None, None)`.
    pub fn ids(&self) -> (Option<String>, Option<String>) {
        let src = if self.modalias.contains(":v") { &self.modalias } else { &self.phys };
        let chars: Vec<char> = src.chars().collect();
        let hex = |c: char| c.is_ascii_hexdigit();
        let mut vendor = None;
        let mut product = None;
        let mut i = 0;
        while i + 4 < chars.len() {
            let digits: String = chars[i + 1..i + 5].iter().collect();
            if digits.chars().all(hex) {
                if chars[i] == 'v' && vendor.is_none() {
                    vendor = Some(u32::from_str_radix(&digits, 16).unwrap_or(0).to_string());
                    i += 5;
                    continue;
                }
                if chars[i] == 'p' && product.is_none() {
                    product = Some(u32::from_str_radix(&digits, 16).unwrap_or(0).to_string());
                    i += 5;
                    continue;
                }
            }
            i += 1;
        }
        (vendor, product)
    }

    /// `usb:v045Ep028Ed…` -> `045e:028e`, the form `lsusb` and a web search use.
    pub fn vid_pid(&self) -> Option<String> {
        let (v, p) = self.ids();
        match (v, p) {
            (Some(v), Some(p)) => Some(format!(
                "{:04x}:{:04x}",
                v.parse::<u32>().ok()?,
                p.parse::<u32>().ok()?
            )),
            _ => None,
        }
    }

    /// How the device is attached, from its phys path.
    pub fn bus(&self) -> &'static str {
        let p = self.phys.as_str();
        if p.starts_with("usb") {
            "USB"
        } else if p.starts_with("pci") {
            "PCI"
        } else if p.starts_with("acpi") {
            "ACPI"
        } else if p.starts_with("platform") {
            "platform"
        } else if p.starts_with("input") || p.contains("bluetooth") {
            "Bluetooth"
        } else if p.starts_with("ALSA") {
            "audio"
        } else if p.is_empty() {
            "unknown"
        } else {
            "other"
        }
    }

    /// One line under the name: `Gamepad · USB · 2 sticks, 12 buttons`.
    pub fn summary(&self) -> String {
        format!("{} · {} · {}", self.kind.title(), self.bus(), self.caps.summary())
    }

    /// The test panel can show this device: it reports something a person can
    /// press, move or switch. A device that reports nothing (a bare power
    /// button with no keys) has nothing to test.
    pub fn testable(&self) -> bool {
        !(self.caps.keys.is_empty()
            && self.caps.abs.is_empty()
            && self.caps.rel.is_empty()
            && self.caps.sw.is_empty()
            && self.caps.led.is_empty()
            && self.caps.ff.is_empty())
    }

    /// The device vanished while it was open: evdev answers `ENODEV`.
    pub fn is_gone_message(text: &str) -> bool {
        text.contains("No such device") || text.contains("ENODEV")
    }
}

/// Turn an open failure into the sentence a person can act on.
///
/// The list always works (sysfs is world-readable); only the live panel needs
/// the node, and udev gives it to group `input` at mode 0660. A user outside
/// that group gets a bare `Permission denied` from the kernel, which names
/// nothing: say which group, and that membership needs a new session.
pub fn explain_open_error(text: &str) -> String {
    if text.contains("Permission denied") || text.contains("EACCES") {
        return format!(
            "{text} — reading a controller needs the `input` group (udev gives \
             /dev/input/* to it at 0660). Add yourself with `sudo gpasswd -a $USER input` \
             and start a new session; the list above works without it."
        );
    }
    text.to_string()
}

// --- events ----------------------------------------------------------------

/// One decoded evdev event.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ev {
    Abs { code: u16, value: i32 },
    Rel { code: u16, value: i32 },
    Key { code: u16, pressed: bool },
    Sw { code: u16, on: bool },
    /// EV_MSC and anything the panel does not draw.
    Other { kind: u16, code: u16, value: i32 },
}

/// The size of one `struct input_event` on this machine: two `long` (the
/// timestamp) plus `u16` type, `u16` code, `i32` value.
pub const EVENT_SIZE: usize = std::mem::size_of::<i64>() * 2 + 8;

/// Decode one `struct input_event` from raw bytes.
///
/// Short input is `None`: a partial read at the end of a pipe must not be
/// mistaken for an event.
pub fn decode_event(bytes: &[u8]) -> Option<Ev> {
    if bytes.len() < EVENT_SIZE {
        return None;
    }
    let kind = u16::from_le_bytes([bytes[16], bytes[17]]);
    let code = u16::from_le_bytes([bytes[18], bytes[19]]);
    let value = i32::from_le_bytes([bytes[20], bytes[21], bytes[22], bytes[23]]);
    Some(match kind {
        EV_ABS => Ev::Abs { code, value },
        EV_REL => Ev::Rel { code, value },
        EV_KEY => Ev::Key { code, pressed: value != 0 },
        EV_SW => Ev::Sw { code, on: value != 0 },
        _ => Ev::Other { kind, code, value },
    })
}

/// Encode an event the way the kernel writes it (used by the tests and by
/// `--selftest`, which drives the panel with a synthetic pad).
pub fn encode_event(kind: u16, code: u16, value: i32) -> Vec<u8> {
    let mut out = vec![0u8; EVENT_SIZE];
    out[16..18].copy_from_slice(&kind.to_le_bytes());
    out[18..20].copy_from_slice(&code.to_le_bytes());
    out[20..24].copy_from_slice(&value.to_le_bytes());
    out
}

/// A live reading of one device: what is pressed, where the axes are, and the
/// extremes seen since the panel was opened (the calibration read).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PadState {
    pub axes: Vec<(u16, i32)>,
    pub buttons: Vec<(u16, bool)>,
    pub hats: Vec<(u16, u16, i8)>,
    pub switches: Vec<(u16, bool)>,
    pub rel: Vec<(u16, i32)>,
    /// axis code -> (lowest, highest) value seen.
    pub range: Vec<(u16, (i32, i32))>,
    pub last: Option<Ev>,
    pub events: u64,
}

impl PadState {
    /// A fresh panel for a device: every axis centred, nothing pressed.
    pub fn new(pad: &Pad) -> Self {
        let mut s = PadState::default();
        for code in pad.caps.shown_axes() {
            let centre = if is_signed_axis(code) { 0 } else { axis_mid(code) };
            s.axes.push((code, centre));
            s.range.push((code, (centre, centre)));
        }
        for code in pad.caps.game_buttons() {
            s.buttons.push((code, false));
        }
        for code in pad
            .caps
            .keys
            .iter()
            .filter(|k| **k >= BTN_MISC && !GAME_BUTTONS.contains(k))
        {
            s.buttons.push((*code, false));
        }
        for (x, y) in pad.caps.hats() {
            s.hats.push((x, y, 0));
        }
        for code in &pad.caps.sw {
            s.switches.push((*code, false));
        }
        for code in [REL_X, REL_Y, REL_WHEEL] {
            if pad.caps.has_rel(code) {
                s.rel.push((code, 0));
            }
        }
        s
    }

    /// Apply one event. Returns false when it changed nothing the panel draws.
    pub fn push(&mut self, ev: Ev) -> bool {
        self.events += 1;
        match ev {
            Ev::Abs { code, value } => {
                let mut seen = false;
                for (c, v) in self.axes.iter_mut() {
                    if *c == code {
                        *v = value;
                        seen = true;
                        break;
                    }
                }
                if !seen {
                    self.axes.push((code, value));
                }
                let r = self
                    .range
                    .iter_mut()
                    .find(|(c, _)| *c == code)
                    .map(|(_, r)| {
                        r.0 = r.0.min(value);
                        r.1 = r.1.max(value);
                    });
                if r.is_none() {
                    self.range.push((code, (value, value)));
                }
                self.apply_hat(code, value);
                self.last = Some(ev);
                true
            }
            Ev::Rel { code, value } => {
                let mut seen = false;
                for (c, v) in self.rel.iter_mut() {
                    if *c == code {
                        *v = value;
                        seen = true;
                        break;
                    }
                }
                if !seen {
                    self.rel.push((code, value));
                }
                self.last = Some(ev);
                true
            }
            Ev::Key { code, pressed } => {
                let mut seen = false;
                for (c, v) in self.buttons.iter_mut() {
                    if *c == code {
                        *v = pressed;
                        seen = true;
                        break;
                    }
                }
                if !seen {
                    self.buttons.push((code, pressed));
                }
                self.last = Some(ev);
                true
            }
            Ev::Sw { code, on } => {
                let mut seen = false;
                for (c, v) in self.switches.iter_mut() {
                    if *c == code {
                        *v = on;
                        seen = true;
                        break;
                    }
                }
                if !seen {
                    self.switches.push((code, on));
                }
                self.last = Some(ev);
                true
            }
            Ev::Other { .. } => false,
        }
    }

    /// A hat is two axes; fold them into one direction for the panel.
    /// A hat is a *pair* of axes, and the kernel sends one event per axis: the
    /// direction is folded from both axes' current values, so a D-pad press
    /// (X then Y, in either order, diagonals included) reads as one direction.
    fn apply_hat(&mut self, code: u16, value: i32) {
        let hit = self.hats.iter().position(|(x, y, _)| *x == code || *y == code);
        if let Some(i) = hit {
            let (x, y) = (self.hats[i].0, self.hats[i].1);
            let dx = if x == code { value } else { self.axis(x).unwrap_or(0) };
            let dy = if y == code { value } else { self.axis(y).unwrap_or(0) };
            self.hats[i].2 = hat_direction(dx, dy);
            return;
        }
        // A lone axis that is not paired: keep it as its own hat entry.
        if (ABS_HAT0X..=ABS_HAT2Y).contains(&code) {
            self.hats.push((code, code, value.signum() as i8));
        }
    }

    pub fn axis(&self, code: u16) -> Option<i32> {
        self.axes.iter().find(|(c, _)| *c == code).map(|(_, v)| *v)
    }

    pub fn pressed(&self, code: u16) -> bool {
        self.buttons.iter().any(|(c, v)| *c == code && *v)
    }

    pub fn switch_on(&self, code: u16) -> bool {
        self.switches.iter().any(|(c, v)| *c == code && *v)
    }

    pub fn range(&self, code: u16) -> Option<(i32, i32)> {
        self.range.iter().find(|(c, _)| *c == code).map(|(_, r)| *r)
    }

    /// An axis mapped to -1.0..1.0 for the panel. Triggers and pedals read
    /// 0..max, so they map 0.0..1.0 from their own minimum.
    pub fn norm(&self, code: u16, info: AxisInfo) -> f32 {
        let value = self.axis(code).unwrap_or(info.flat());
        let (lo, hi) = if is_signed_axis(code) {
            (info.min.min(-1), info.max.max(1))
        } else {
            (info.min, info.max)
        };
        if hi == lo {
            return 0.0;
        }
        if is_signed_axis(code) {
            let scale = (-lo).min(hi).max(1);
            ((value as f32) / scale as f32).clamp(-1.0, 1.0)
        } else {
            ((value - lo) as f32 / (hi - lo) as f32).clamp(0.0, 1.0)
        }
    }

    /// One line of the event log: `Right stick X 12734  (of -32768..32767)`.
    pub fn describe(&self, ev: Ev) -> String {
        match ev {
            Ev::Abs { code, value } => {
                let range = self
                    .range(code)
                    .map(|(lo, hi)| format!(" (seen {lo}..{hi})"))
                    .unwrap_or_default();
                format!("{} {value}{range}", axis_name(code))
            }
            Ev::Rel { code, value } => format!("relative {} {value}", rel_name(code)),
            Ev::Key { code, pressed } => {
                format!("{} {}", button_name(code), if pressed { "pressed" } else { "released" })
            }
            Ev::Sw { code, on } => format!("switch {code} {}", if on { "on" } else { "off" }),
            Ev::Other { kind, code, value } => format!("event type {kind} code {code} value {value}"),
        }
    }
}

/// Fold a hat's two axis values into one direction.
///
/// Linux grows Y downwards, so `(0, -1)` is up. `-1` is "at rest".
pub fn hat_direction(dx: i32, dy: i32) -> i8 {
    match (dx.signum(), dy.signum()) {
        (1, 0) => 1,   // right
        (-1, 0) => 3,  // left
        (0, 1) => 2,   // down
        (0, -1) => 0,  // up
        (1, -1) => 4,  // up-right
        (1, 1) => 5,   // down-right
        (-1, -1) => 6, // up-left
        (-1, 1) => 7,  // down-left
        _ => -1,
    }
}

/// The name of a folded hat direction, for the event log.
pub fn hat_name(dir: i8) -> &'static str {
    match dir {
        0 => "up",
        1 => "right",
        2 => "down",
        3 => "left",
        4 => "up-right",
        5 => "down-right",
        6 => "up-left",
        7 => "down-left",
        _ => "at rest",
    }
}

/// Axes that read signed and centre on 0; the rest read from their minimum.
pub fn is_signed_axis(code: u16) -> bool {
    matches!(
        code,
        ABS_X | ABS_Y | ABS_RX | ABS_RY | ABS_HAT0X | ABS_HAT0Y | ABS_HAT1X | ABS_HAT1Y
            | ABS_HAT2X | ABS_HAT2Y | ABS_STEER | ABS_WHEEL | ABS_MT_POSITION_X | ABS_MT_POSITION_Y
    )
}

fn axis_mid(_code: u16) -> i32 {
    // Triggers and pedals rest in the middle of their range on every pad we
    // test (0..255 -> 127); the panel starts there so it reads "at rest".
    127
}

/// `EVIOCGABS`' answer for one axis.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct AxisInfo {
    pub value: i32,
    pub min: i32,
    pub max: i32,
    pub flat: i32,
    pub fuzz: i32,
}

impl AxisInfo {
    /// The value the axis rests at.
    pub fn flat(&self) -> i32 {
        if is_signed_axis(0) {
            0
        } else {
            0
        }
    }
}

// --- sysfs parsing ---------------------------------------------------------

/// Parse one sysfs capability file (`/sys/class/input/<dev>/device/capabilities/<ev>`).
///
/// The kernel prints a bitmask as big-endian hex words, most significant word
/// first: `"00000000 00000000 00000000 00000000 00000000 00000000 00000000 003e0000"`.
/// Word 0 of the output is the highest word index, so the bit index is
/// `(word_index_from_right * 32) + bit_in_word`.
pub fn parse_caps_words(text: &str) -> BTreeSet<u16> {
    let words: Vec<&str> = text.split_whitespace().collect();
    let mut out = BTreeSet::new();
    for (i, word) in words.iter().enumerate() {
        let value = match u64::from_str_radix(word, 16) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let base = ((words.len() - 1 - i) * 32) as u32;
        for bit in 0..32u32 {
            if value & (1u64 << bit) != 0 {
                let index = base + bit;
                if index <= u32::from(u16::MAX) {
                    out.insert(index as u16);
                }
            }
        }
    }
    out
}

/// Build a [`DeviceInfo`] from the strings sysfs exposes for one device.
pub fn device_from_sysfs(path: String, sys: String, fields: [String; 4]) -> DeviceInfo {
    let [name, phys, modalias, caps_text] = fields;
    DeviceInfo {
        path,
        sys,
        name,
        phys,
        modalias,
        caps: caps_from_sysfs(&caps_text),
    }
}

/// `caps_text` is the four capability files joined with `|` markers, in the
/// order the kernel writes them: ev, key, rel, abs, msc, sw, led, ff.
pub fn caps_from_sysfs(text: &str) -> Caps {
    let mut caps = Caps::default();
    for line in text.split('\n') {
        let (group, words) = match line.split_once(' ') {
            Some(pair) => pair,
            None => continue,
        };
        let set = parse_caps_words(words);
        match group {
            "ev" => caps.ev = set,
            "key" => caps.keys = set,
            "rel" => caps.rel = set,
            "abs" => caps.abs = set,
            "msc" => caps.msc = set,
            "sw" => caps.sw = set,
            "led" => caps.led = set,
            "ff" => caps.ff = set,
            _ => {}
        }
    }
    caps
}

/// Render a [`Caps`] the way sysfs prints its four capability files, joined by
/// the group names — the input [`caps_from_sysfs`] accepts, so the round trip
/// (`caps_from_sysfs(&format_caps_sysfs(&caps)) == caps`) is a test.
pub fn format_caps_sysfs(caps: &Caps) -> String {
    fn words(set: &BTreeSet<u16>) -> String {
        if set.is_empty() {
            return "0".into();
        }
        let top = *set.iter().next_back().unwrap();
        let words = (top as usize / 32) + 1;
        let mut values = vec![0u64; words];
        for code in set {
            values[*code as usize / 32] |= 1u64 << (*code % 32);
        }
        values
            .iter()
            .rev()
            .map(|v| format!("{v:08x}"))
            .collect::<Vec<_>>()
            .join(" ")
    }
    format!(
        "ev {}\nkey {}\nrel {}\nabs {}\nmsc {}\nsw {}\nled {}\nff {}",
        words(&caps.ev),
        words(&caps.keys),
        words(&caps.rel),
        words(&caps.abs),
        words(&caps.msc),
        words(&caps.sw),
        words(&caps.led),
        words(&caps.ff)
    )
}

// --- the app's list --------------------------------------------------------

/// Split a device list into the controllers the app headlines and the rest it
/// lists under "Other input devices".
pub fn split_pads(pads: Vec<Pad>) -> (Vec<Pad>, Vec<Pad>) {
    let (controllers, other): (Vec<Pad>, Vec<Pad>) =
        pads.into_iter().partition(|p| p.kind.is_controller());
    (controllers, other)
}

/// Drop duplicate nodes of the same physical device (a pad shows up as one
/// evdev node per interface; a combo device as several).
pub fn dedup_pads(pads: Vec<Pad>) -> Vec<Pad> {
    let mut seen: BTreeSet<String> = BTreeSet::new();
    pads.into_iter()
        .filter(|p| seen.insert(format!("{}|{}", p.name, p.phys)))
        .collect()
}

/// The text `--list` prints.
pub fn format_pads(pads: &[Pad]) -> String {
    let (controllers, other) = split_pads(pads.to_vec());
    let mut out = String::new();
    if controllers.is_empty() {
        out.push_str("no game controllers\n");
    } else {
        out.push_str("game controllers:\n");
        for p in &controllers {
            out.push_str(&format_pad(p));
        }
    }
    if !other.is_empty() {
        out.push_str("other input devices:\n");
        for p in &other {
            out.push_str(&format_pad(p));
        }
    }
    out
}

fn format_pad(p: &Pad) -> String {
    let ids = p.vid_pid().map(|i| format!(" {i}")).unwrap_or_default();
    format!(
        "  {}  {} [{}]{} {}\n      {}\n",
        p.path, p.name, p.kind.title(), ids, p.bus(), p.caps.summary()
    )
}

/// The JSON `--json` prints: one object per device, keys in a fixed order so
/// the output is diffable.
pub fn to_json(pads: &[Pad]) -> String {
    if pads.is_empty() {
        return "[]\n".into();
    }
    let mut out = String::from("[\n");
    for (i, p) in pads.iter().enumerate() {
        out.push_str(&format!(
            "  {{\"path\":{},\"sys\":{},\"name\":{},\"kind\":{},\"bus\":{},\"ids\":{},\
             \"phys\":{},\"caps\":{},\"testable\":{}}}{}",
            json_str(&p.path),
            json_str(&p.sys),
            json_str(&p.name),
            json_str(p.kind.title()),
            json_str(p.bus()),
            match p.vid_pid() {
                Some(v) => json_str(&v),
                None => "null".to_string(),
            },
            json_str(&p.phys),
            json_str(&p.caps.summary()),
            p.testable(),
            if i + 1 == pads.len() { "" } else { "," }
        ));
        out.push('\n');
    }
    out.push_str("]\n");
    out
}

fn json_str(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

// --- rumble ---------------------------------------------------------------

/// A rumble request for the joystick device (`/dev/input/jsN`), the evdev
/// force-feedback effect the kernel's ff-memset layer accepts.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Rumble {
    /// 0..32767, low-frequency motor.
    pub weak: u16,
    /// 0..32767, high-frequency motor.
    pub strong: u16,
}

/// Clamp a requested magnitude into the range the kernel accepts.
pub fn rumble_level(value: i32) -> u16 {
    value.clamp(0, 32767) as u16
}

#[cfg(feature = "evdev")]
pub mod raw;

#[cfg(feature = "cosmic")]
pub mod app;

pub mod layout;

#[cfg(test)]
mod tests;
