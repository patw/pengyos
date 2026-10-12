//! Unit tests for the std-only half: classification, names, the sysfs bitmask
//! round trip, the event decoder, the live state, the panel layout, the output
//! formats. No display, no /dev, no D-Bus.

use super::*;
use std::collections::BTreeSet;

fn set(v: &[u16]) -> BTreeSet<u16> {
    v.iter().copied().collect()
}

/// A standard modern gamepad: two sticks, a D-pad, four face buttons, two
/// bumpers, two triggers, Select/Start/Guide, both stick-in buttons, rumble.
fn gamepad_caps() -> Caps {
    Caps {
        ev: set(&[EV_KEY, EV_ABS, EV_MSC]),
        keys: set(&[
            BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE,
            BTN_THUMBL, BTN_THUMBR,
        ]),
        abs: set(&[ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_Z, ABS_RZ, ABS_HAT0X, ABS_HAT0Y]),
        msc: set(&[4]),
        ..Default::default()
    }
}

#[test]
fn test_classify_gamepad() {
    assert_eq!(classify(&gamepad_caps()), Kind::Gamepad);
    assert!(classify(&gamepad_caps()).is_controller());
}

#[test]
fn test_classify_shapes() {
    // A mouse: relative motion plus the mouse button, and the ABS pair some
    // mice report. It must never read as a joystick.
    let mouse = Caps {
        ev: set(&[EV_KEY, EV_REL, EV_ABS]),
        keys: set(&[BTN_MOUSE, BTN_RIGHT, BTN_MIDDLE, BTN_SIDE, BTN_EXTRA]),
        rel: set(&[REL_X, REL_Y, REL_WHEEL]),
        abs: set(&[ABS_X, ABS_Y]),
        ..Default::default()
    };
    assert_eq!(classify(&mouse), Kind::Mouse);

    // A keyboard: many keys below BTN_MISC, including KEY_A.
    let mut keys: Vec<u16> = (KEY_A..KEY_A + 40).collect();
    keys.push(KEY_ESC);
    let keyboard = Caps { ev: set(&[EV_KEY]), keys: set(&keys), ..Default::default() };
    assert_eq!(classify(&keyboard), Kind::Keyboard);

    // A media remote: only keys at BTN_MISC and above, no axes.
    let remote = Caps {
        ev: set(&[EV_KEY]),
        keys: set(&[BTN_A, BTN_B, BTN_SELECT, BTN_START, BTN_TRIGGER]),
        ..Default::default()
    };
    assert_eq!(classify(&remote), Kind::Remote);

    // An audio jack: only SND_KEY_* (0x160+).
    let audio = Caps { ev: set(&[EV_KEY]), keys: set(&[0x162, 0x163, 0x164]), ..Default::default() };
    assert_eq!(classify(&audio), Kind::Remote);
    assert_eq!(Kind::Remote.icon(), "input-keyboard");

    // A headphone jack with an insertion sensor reports switches and nothing else.
    let jack = Caps { ev: set(&[EV_SW]), sw: set(&[0]), ..Default::default() };
    assert_eq!(classify(&jack), Kind::Audio);
    assert_eq!(Kind::Audio.icon(), "audio-headphones");
    assert!(!Kind::Audio.is_controller());

    // A wheel, pedals, a throttle quadrant, a flight stick, a touch screen.
    let wheel = Caps {
        ev: set(&[EV_KEY, EV_ABS]),
        keys: set(&[BTN_A, BTN_B, BTN_TL, BTN_TR, BTN_THUMBL]),
        abs: set(&[ABS_WHEEL, ABS_GAS, ABS_BRAKE, ABS_X, ABS_Y, ABS_Z, ABS_RZ]),
        ..Default::default()
    };
    assert_eq!(classify(&wheel), Kind::Wheel);

    // Gas + brake is a pedal set, whatever else rides with it: a modern pedal
    // set carries a throttle axis too, and calling it a "throttle quadrant"
    // would hide the brake. A quadrant has no brake.
    let pedals = Caps {
        ev: set(&[EV_ABS]),
        abs: set(&[ABS_GAS, ABS_BRAKE, ABS_THROTTLE]),
        ..Default::default()
    };
    assert_eq!(classify(&pedals), Kind::Pedals);

    let quadrant = Caps {
        ev: set(&[EV_ABS]),
        abs: set(&[ABS_THROTTLE, ABS_RUDDER, ABS_RZ]),
        ..Default::default()
    };
    assert_eq!(classify(&quadrant), Kind::Throttle);

    let pedals2 = Caps { ev: set(&[EV_ABS]), abs: set(&[ABS_GAS, ABS_BRAKE]), ..Default::default() };
    assert_eq!(classify(&pedals2), Kind::Pedals);

    let flight = Caps {
        ev: set(&[EV_KEY, EV_ABS]),
        keys: set(&[BTN_TRIGGER, BTN_THUMB, BTN_BASE]),
        abs: set(&[ABS_X, ABS_Y, ABS_Z, ABS_RZ, ABS_HAT0X, ABS_HAT0Y]),
        ..Default::default()
    };
    assert_eq!(classify(&flight), Kind::Flight);

    let touch = Caps {
        ev: set(&[EV_KEY, EV_ABS]),
        keys: set(&[BTN_TOUCH, BTN_TOOL_FINGER]),
        abs: set(&[ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_PRESSURE]),
        ..Default::default()
    };
    assert_eq!(classify(&touch), Kind::Touch);

    // A bare power button: one key, no axes. Not a controller, not a remote.
    let power = Caps { ev: set(&[EV_KEY]), keys: set(&[KEY_POWER]), ..Default::default() };
    assert_eq!(classify(&power), Kind::Other);

    // Nothing at all.
    assert_eq!(classify(&Caps::default()), Kind::Other);
}

#[test]
fn test_sysfs_bitmask_round_trip() {
    let caps = gamepad_caps();
    let text = format_caps_sysfs(&caps);
    assert_eq!(caps_from_sysfs(&text), caps);
}

#[test]
fn test_sysfs_bitmask_word_order() {
    // The kernel prints the highest word first, as 64-bit words: BTN_A (0x130)
    // is bit 304, i.e. word 4, bit 48 -> the first of a 5-word key mask.
    let caps = Caps { ev: set(&[EV_KEY]), keys: set(&[BTN_A]), ..Default::default() };
    let text = format_caps_sysfs(&caps);
    let line = text.lines().find(|l| l.starts_with("key ")).unwrap();
    let words: Vec<&str> = line.split_whitespace().skip(1).collect();
    assert_eq!(words.len(), 5);
    assert_eq!(words[0], "1000000000000");
    assert_eq!(words[4], "0");
    assert_eq!(caps_from_sysfs(&text).keys, set(&[BTN_A]));
}

#[test]
fn test_parse_caps_words_zero() {
    assert!(parse_caps_words("0").is_empty());
    assert!(parse_caps_words("").is_empty());
    assert_eq!(parse_caps_words("00000000 00000001"), set(&[0]));
    assert_eq!(parse_caps_words("1 0"), set(&[64]));
    assert_eq!(parse_caps_words_width("00000001 00000000", 32), set(&[32]));
}

/// The real sysfs text of a Logitech G305 receiver (behoweather, 2026-10-12):
/// 64-bit words, leading zeros dropped. It has keyboard keys too (the
/// receiver doubles as one), but relative X/Y plus BTN_LEFT make it a mouse.
#[test]
fn test_real_g305_sysfs_reads_as_a_mouse() {
    let keys = parse_caps_words(
        "3f00733fff 0 0 483ffff17aff32d bfd4444600000000 ffff0001 130ff38b17d007 \
         ffff7bfad9415fff ffbeffdfffefffff fffffffffffffffe",
    );
    assert!(keys.contains(&0x110), "BTN_LEFT");
    assert!(keys.contains(&0x114), "BTN_EXTRA");
    let caps = Caps {
        ev: parse_caps_words("12001f"),
        keys,
        rel: parse_caps_words("1943"),
        abs: parse_caps_words("100000000"),
        ..Caps::default()
    };
    assert_eq!(classify(&caps), Kind::Mouse);
    // A keyboard's mouse interface: five buttons at BTN_LEFT.. and REL_X/Y.
    let mouse_if = Caps {
        ev: parse_caps_words("17"),
        keys: parse_caps_words("1f0000 0 0 0 0"),
        rel: parse_caps_words("1943"),
        ..Caps::default()
    };
    assert_eq!(classify(&mouse_if), Kind::Mouse);
}

#[test]
fn test_event_decode_and_encode() {
    let bytes = encode_event(EV_ABS, ABS_RX, -1234);
    assert_eq!(bytes.len(), EVENT_SIZE);
    assert_eq!(
        decode_event(&bytes),
        Some(Ev::Abs { code: ABS_RX, value: -1234 })
    );
    let press = encode_event(EV_KEY, BTN_A, 1);
    assert_eq!(decode_event(&press), Some(Ev::Key { code: BTN_A, pressed: true }));
    let release = encode_event(EV_KEY, BTN_A, 0);
    assert_eq!(decode_event(&release), Some(Ev::Key { code: BTN_A, pressed: false }));
    // A short buffer is not an event.
    assert_eq!(decode_event(&bytes[..8]), None);
    assert_eq!(decode_event(&[]), None);
}

#[test]
fn test_pad_state_tracks_the_device() {
    let pad = DeviceInfo {
        path: "/dev/input/event13".into(),
        sys: "input13".into(),
        name: "Microsoft XBox Controller".into(),
        phys: "usb-0000:00:14.0-3/input0".into(),
        modalias: "usb:v045Ep028Ed0700dc00dsc00p00ic01isc02ip00in01".into(),
        caps: gamepad_caps(),
    }
    .pad();
    let mut state = PadState::new(&pad);
    assert_eq!(state.axis(ABS_X), Some(0));
    assert_eq!(state.axis(ABS_Z), Some(0), "a trigger starts released");
    assert!(!state.pressed(BTN_A));

    state.push(Ev::Key { code: BTN_A, pressed: true });
    assert!(state.pressed(BTN_A));
    state.push(Ev::Key { code: BTN_A, pressed: false });
    assert!(!state.pressed(BTN_A));

    state.push(Ev::Abs { code: ABS_X, value: 12000 });
    assert_eq!(state.axis(ABS_X), Some(12000));
    assert_eq!(state.range(ABS_X), Some((0, 12000)));
    state.push(Ev::Abs { code: ABS_X, value: -30000 });
    assert_eq!(state.range(ABS_X), Some((-30000, 12000)));
    assert_eq!(state.events, 4);

    // The D-pad folds into one direction, from BOTH axes' current values:
    // the kernel sends one event per axis, in either order.
    state.push(Ev::Abs { code: ABS_HAT0X, value: -1 });
    assert_eq!(state.hats.first().map(|h| h.2), Some(3)); // left
    state.push(Ev::Abs { code: ABS_HAT0Y, value: 1 });
    assert_eq!(state.hats.first().map(|h| h.2), Some(7)); // down-left
    state.push(Ev::Abs { code: ABS_HAT0X, value: 0 });
    assert_eq!(state.hats.first().map(|h| h.2), Some(2)); // down
    state.push(Ev::Abs { code: ABS_HAT0Y, value: 0 });
    assert_eq!(state.hats.first().map(|h| h.2), Some(-1)); // at rest
    assert_eq!(hat_name(7), "down-left");
    assert_eq!(hat_name(-1), "at rest");
    assert!(state.describe(Ev::Abs { code: ABS_HAT0X, value: -1 }).contains("D-pad"));
}

#[test]
fn test_pad_state_norm_uses_the_device_range() {
    let pad = DeviceInfo { caps: gamepad_caps(), ..Default::default() }.pad();
    let mut state = PadState::new(&pad);
    let stick = AxisInfo { min: -32768, max: 32767, ..Default::default() };
    let trigger = AxisInfo { min: 0, max: 255, ..Default::default() };

    state.push(Ev::Abs { code: ABS_X, value: -32768 });
    assert_eq!(state.norm(ABS_X, stick), -1.0);
    state.push(Ev::Abs { code: ABS_X, value: 32767 });
    assert_eq!(state.norm(ABS_X, stick), 1.0);
    state.push(Ev::Abs { code: ABS_X, value: 0 });
    assert_eq!(state.norm(ABS_X, stick), 0.0);

    // A trigger reads 0..1 from its own minimum, not -1..1.
    state.push(Ev::Abs { code: ABS_Z, value: 0 });
    assert_eq!(state.norm(ABS_Z, trigger), 0.0);
    state.push(Ev::Abs { code: ABS_Z, value: 255 });
    assert_eq!(state.norm(ABS_Z, trigger), 1.0);

    // Out-of-range values clamp instead of overflowing the bar.
    state.push(Ev::Abs { code: ABS_Z, value: 900 });
    assert_eq!(state.norm(ABS_Z, trigger), 1.0);
    // A device that reports no range at all answers 0, never NaN.
    assert_eq!(state.norm(ABS_X, AxisInfo::default()), 0.0);
}

#[test]
fn test_ids_and_bus() {
    let pad = DeviceInfo {
        path: "/dev/input/event13".into(),
        sys: "input13".into(),
        name: "Controller".into(),
        phys: "usb-0000:00:14.0-3/input0".into(),
        modalias: "usb:v045Ep028Ed0700dc00dsc00p00ic01isc02ip00in01".into(),
        caps: gamepad_caps(),
    }
    .pad();
    assert_eq!(pad.vid_pid().as_deref(), Some("045e:028e"));
    assert_eq!(pad.bus(), "USB");

    // A platform device carries no ids and no bus we can name.
    let wmi = DeviceInfo {
        path: "/dev/input/event2".into(),
        sys: "input2".into(),
        name: "Eee PC WMI hotkeys".into(),
        phys: "eeepc-wmi/input0".into(),
        modalias: String::new(),
        caps: Caps { ev: set(&[EV_KEY]), keys: set(&[KEY_A, KEY_ESC]), ..Default::default() },
    }
    .pad();
    assert_eq!(wmi.vid_pid(), None);
    assert_eq!(wmi.bus(), "other");

    // A paired Bluetooth pad reports the HID device's phys.
    let bt = DeviceInfo {
        phys: "input17:00:1a:7b:9c:22:4e".into(),
        ..Default::default()
    }
    .pad();
    assert_eq!(bt.bus(), "Bluetooth");
}

#[test]
fn test_summary_names_the_shape() {
    let pad = DeviceInfo { caps: gamepad_caps(), ..Default::default() }.pad();
    let s = pad.summary();
    assert!(s.contains("Gamepad"), "{s}");
    assert!(s.contains("2 sticks"), "{s}");
    assert!(s.contains("11 buttons"), "{s}");
    assert!(s.contains("D-pad"), "{s}");
    assert!(s.contains("2 trigger"), "{s}");
}

#[test]
fn test_split_and_dedup() {
    let pads = vec![
        DeviceInfo { path: "/dev/input/event1".into(), name: "Keyboard".into(), caps: Caps {
            ev: set(&[EV_KEY]), keys: set(&(10..60).collect::<Vec<u16>>()), ..Default::default()
        }, ..Default::default() }.pad(),
        DeviceInfo { path: "/dev/input/event2".into(), name: "Pad".into(), caps: gamepad_caps(), ..Default::default() }.pad(),
        DeviceInfo { path: "/dev/input/event3".into(), name: "Pad".into(), caps: gamepad_caps(), ..Default::default() }.pad(),
    ];
    let deduped = dedup_pads(pads.clone());
    assert_eq!(deduped.len(), 2, "the same name+phys is one device");
    let (controllers, other) = split_pads(deduped);
    assert_eq!(controllers.len(), 1);
    assert_eq!(other.len(), 1);
    assert_eq!(other[0].kind, Kind::Keyboard);
}

#[test]
fn test_list_output_names_the_kind() {
    let pads = vec![DeviceInfo {
        path: "/dev/input/event13".into(),
        sys: "input13".into(),
        name: "Microsoft XBox Controller".into(),
        phys: "usb-0000:00:14.0-3/input0".into(),
        modalias: "usb:v045Ep028Ed0700".into(),
        caps: gamepad_caps(),
    }
    .pad()];
    let text = format_pads(&pads);
    assert!(text.starts_with("game controllers:"), "{text}");
    assert!(text.contains("/dev/input/event13"), "{text}");
    assert!(text.contains("045e:028e"), "{text}");
    // The negative control: an empty list says so, it is not silent.
    assert!(format_pads(&[]).contains("no game controllers"));
}

#[test]
fn test_json_output_is_json() {
    let pads = vec![DeviceInfo {
        path: "/dev/input/event13".into(),
        name: "Pad \"quoted\"".into(),
        caps: gamepad_caps(),
        ..Default::default()
    }
    .pad()];
    let json = to_json(&pads);
    assert!(json.starts_with("[\n") && json.trim_end().ends_with(']'), "{json}");
    assert!(json.contains("\"path\":\"/dev/input/event13\""), "{json}");
    assert!(json.contains("Pad \\\"quoted\\\""), "{json}");
    assert!(json.contains("\"ids\":null"), "{json}");
    assert!(to_json(&[]).contains("[]"));
}

#[test]
fn test_layout_places_what_the_device_has() {
    let caps = gamepad_caps();
    let l = layout::layout(&caps, Kind::Gamepad);
    // Both sticks, the hat, the four face buttons, both bumpers, both triggers,
    // Select/Start/Guide and both stick-in buttons.
    let buttons: Vec<u16> = l
        .elements
        .iter()
        .filter_map(|e| match e {
            layout::Piece::Button { code, .. } => Some(*code),
            _ => None,
        })
        .collect();
    for code in [BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE] {
        assert!(buttons.contains(&code), "missing button {code}");
    }
    let bars: Vec<u16> = l
        .elements
        .iter()
        .filter_map(|e| match e {
            layout::Piece::Bar { code, .. } => Some(*code),
            _ => None,
        })
        .collect();
    assert!(bars.contains(&ABS_Z) && bars.contains(&ABS_RZ));
    assert_eq!(
        l.elements
            .iter()
            .filter(|e| matches!(e, layout::Piece::Stick { .. }))
            .count(),
        2
    );
    assert_eq!(
        l.elements
            .iter()
            .filter(|e| matches!(e, layout::Piece::Hat { .. }))
            .count(),
        1
    );
    // The stick clicks light their stick instead of drawing a shape on it.
    let presses: Vec<Option<u16>> = l
        .elements
        .iter()
        .filter_map(|e| match e {
            layout::Piece::Stick { press, .. } => Some(*press),
            _ => None,
        })
        .collect();
    assert_eq!(presses, vec![Some(BTN_THUMBL), Some(BTN_THUMBR)]);
}

#[test]
fn test_layout_has_nothing_the_device_lacks() {
    // A flight stick: no face buttons, no bumpers, one stick, a hat, a trigger.
    let caps = Caps {
        ev: set(&[EV_KEY, EV_ABS]),
        keys: set(&[BTN_TRIGGER]),
        abs: set(&[ABS_X, ABS_Y, ABS_RZ, ABS_HAT0X, ABS_HAT0Y]),
        ..Default::default()
    };
    let l = layout::layout(&caps, Kind::Flight);
    assert!(l.elements.iter().filter(|e| matches!(e, layout::Piece::Button { .. })).all(|e| match e {
        layout::Piece::Button { code, .. } => *code == BTN_TRIGGER,
        _ => unreachable!(),
    }));
    // The axis the shape has no room for (here: none) is never dropped: a
    // steering axis shows up as a bar.
    let mut wheel = gamepad_caps();
    wheel.abs.insert(ABS_WHEEL);
    wheel.abs.insert(ABS_GAS);
    let l = layout::layout(&wheel, Kind::Wheel);
    let bars: Vec<u16> = l
        .elements
        .iter()
        .filter_map(|e| match e {
            layout::Piece::Bar { code, .. } => Some(*code),
            _ => None,
        })
        .collect();
    assert!(bars.contains(&ABS_WHEEL), "{bars:?}");
    assert!(bars.contains(&ABS_GAS), "{bars:?}");
}

#[test]
fn test_layout_scale_keeps_the_aspect_ratio() {
    let (x, y) = layout::scale(500.0, 280.0, 1000.0, 560.0);
    assert_eq!((x, y), (500.0, 280.0));
    // A narrow canvas: the drawing shrinks and stays centred.
    let (x, y) = layout::scale(500.0, 280.0, 500.0, 560.0);
    assert_eq!((x, y), (250.0, 280.0));
    assert_eq!(layout::scale_len(100.0, 500.0, 560.0), 50.0);
}

#[test]
fn test_permission_error_names_the_group() {
    let e = explain_open_error("/dev/input/event13: Permission denied (os error 13)");
    assert!(e.contains("input"), "{e}");
    assert!(e.contains("gpasswd"), "{e}");
    assert!(e.contains("/dev/input/event13"), "{e}");
    // Any other failure is passed through untouched: inventing an explanation
    // for `ENODEV` would send the user to the wrong fix.
    let gone = explain_open_error("/dev/input/js0: No such device");
    assert_eq!(gone, "/dev/input/js0: No such device");
}

#[test]
fn test_testable_is_a_capability_question() {
    // A bare power button with no keys at all has nothing to test.
    let empty = DeviceInfo { caps: Caps::default(), ..Default::default() }.pad();
    assert!(!empty.testable());
    // A pad, a jack with one switch and a keyboard are all testable.
    let pad = DeviceInfo { caps: gamepad_caps(), ..Default::default() }.pad();
    assert!(pad.testable());
    let jack = DeviceInfo { caps: Caps { ev: set(&[EV_SW]), sw: set(&[0]), ..Default::default() }, ..Default::default() }.pad();
    assert!(jack.testable());
}

#[test]
fn test_names_are_stable() {    assert_eq!(button_name(BTN_A), "A");
    assert_eq!(button_name(BTN_MODE), "Guide");
    assert_eq!(axis_name(ABS_RZ), "Right trigger");
    assert_eq!(axis_name(ABS_WHEEL), "Steering");
    assert_eq!(Kind::Gamepad.title(), "Gamepad");
    // An unknown code is named, never blank.
    assert_eq!(axis_name(0x2ff), "Axis");
    assert_eq!(button_name(0x2ff), "Button");
}

#[test]
fn test_rumble_level_is_clamped() {
    assert_eq!(rumble_level(-5), 0);
    assert_eq!(rumble_level(70000), 65535);
    assert_eq!(rumble_level(100 * 655), 65500);
    assert_eq!(rumble_level(1000), 1000);
}

/// Every ioctl number, against the value <linux/input.h> produces on x86_64
/// (printed by a C program built against the header, 2026-10-12). The first
/// version had the direction bits swapped and the wrong bases for EVIOCGABS
/// and EVIOCSFF/EVIOCRMFF, so no ioctl it sent was the one it meant.
#[test]
fn test_ioctl_numbers_match_the_kernel_header() {
    use crate::ioctl::*;
    assert_eq!(EVIOCSFF, 0x4030_4580);
    assert_eq!(EVIOCRMFF, 0x4004_4581);
    assert_eq!(eviocgabs(0), 0x8018_4540);
    assert_eq!(eviocgabs(5), 0x8018_4545);
    assert_eq!(eviocgbit(EV_KEY, KEY_BITS_LEN as u32), 0x8060_4521);
}

#[test]
fn test_ff_effect_layout_matches_the_kernel() {
    use crate::ioctl::FfEffect;
    // sizeof(struct ff_effect) == 48 and offsetof(u) == 16 on x86_64.
    assert_eq!(std::mem::size_of::<FfEffect>(), 48);
    let e = FfEffect::rumble(0xabcd, 0x1234, 400);
    let base = &e as *const FfEffect as usize;
    assert_eq!(&e.u as *const _ as usize - base, 16);
    assert_eq!(e.type_, 0x50);
    assert_eq!(e.id, -1);
    assert_eq!(e.replay_length, 400);
    assert_eq!(e.rumble_magnitudes(), (0xabcd, 0x1234));
}

#[test]
fn test_ff_play_event_is_an_ev_ff_input_event() {
    // struct input_event: 16 bytes of timeval, then type, code, value.
    let ev = crate::ioctl::ff_play_event(3, true);
    assert_eq!(ev.len(), 24);
    assert_eq!(u16::from_ne_bytes([ev[16], ev[17]]), 0x15); // EV_FF
    assert_eq!(u16::from_ne_bytes([ev[18], ev[19]]), 3); // the effect id
    assert_eq!(i32::from_ne_bytes([ev[20], ev[21], ev[22], ev[23]]), 1);
    let stop = crate::ioctl::ff_play_event(3, false);
    assert_eq!(i32::from_ne_bytes([stop[20], stop[21], stop[22], stop[23]]), 0);
}

#[test]
fn test_gone_message() {
    assert!(Pad::is_gone_message("/dev/input/js0: No such device"));
    assert!(!Pad::is_gone_message("/dev/input/js0: Permission denied"));
}

/// The input codes, against <linux/input-event-codes.h>. 0.1.x had the hat
/// axes at 0x22.. (they are 0x10..), the joystick buttons, BTN_TOUCH and
/// KEY_VOLUMEUP wrong, so a real D-pad was "Axis" and never drew a cross; the
/// other tests passed because they reused the same wrong constants.
#[test]
fn test_input_codes_match_the_kernel_header() {
    assert_eq!((ABS_HAT0X, ABS_HAT0Y, ABS_HAT1X, ABS_HAT2Y), (0x10, 0x11, 0x12, 0x15));
    assert_eq!((ABS_X, ABS_RZ, ABS_THROTTLE, ABS_WHEEL, ABS_GAS, ABS_BRAKE), (0x00, 0x05, 0x06, 0x08, 0x09, 0x0a));
    assert_eq!((BTN_TRIGGER, BTN_THUMB, BTN_PINKIE, BTN_BASE, BTN_DEAD), (0x120, 0x121, 0x125, 0x126, 0x12f));
    assert_eq!((BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_MODE, BTN_THUMBR), (0x130, 0x131, 0x133, 0x134, 0x136, 0x13c, 0x13e));
    assert_eq!((BTN_TOOL_FINGER, BTN_TOUCH, ABS_MT_PRESSURE, KEY_VOLUMEUP), (0x145, 0x14a, 0x3a, 0x73));
}

/// The real sysfs text of an 8BitDo Ultimate 3-mode in Xbox mode (xpad), as
/// read on behoweather 2026-10-12.
fn eightbitdo_caps() -> Caps {
    Caps {
        ev: parse_caps_words("120001b"),
        keys: parse_caps_words("7cdb000000000000 0 8000000000 0 0"),
        abs: parse_caps_words("3003f"),
        ff: set(&[0x50]),
        ..Default::default()
    }
}

#[test]
fn test_real_8bitdo_is_a_gamepad_with_a_dpad() {
    let caps = eightbitdo_caps();
    assert_eq!(classify(&caps), Kind::Gamepad);
    assert_eq!(caps.hats(), vec![(ABS_HAT0X, ABS_HAT0Y)]);
    assert_eq!(caps.right_stick(), Some((ABS_RX, ABS_RY)));
    assert_eq!(caps.triggers(), (Some(ABS_Z), Some(ABS_RZ)));
    let l = layout::layout(&caps, Kind::Gamepad);
    assert_eq!(l.elements.iter().filter(|e| matches!(e, layout::Piece::Hat { .. })).count(), 1);
    // No D-pad axis is drawn as a stray bar.
    assert!(!l.elements.iter().any(|e| matches!(e, layout::Piece::Bar { code, .. } if *code == ABS_HAT0X || *code == ABS_HAT0Y)));
}

#[test]
fn test_xbox_over_bluetooth_mapping() {
    // hid-microsoft: right stick on Z/RZ, triggers on BRAKE/GAS.
    let caps = Caps {
        ev: set(&[EV_KEY, EV_ABS]),
        keys: set(&[BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR]),
        abs: set(&[ABS_X, ABS_Y, ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y]),
        ..Default::default()
    };
    assert_eq!(classify(&caps), Kind::Gamepad, "two sticks win over pedal axes");
    assert_eq!(caps.right_stick(), Some((ABS_Z, ABS_RZ)));
    assert_eq!(caps.triggers(), (Some(ABS_BRAKE), Some(ABS_GAS)));
}

#[test]
fn test_unsigned_stick_rests_centred() {
    let caps = Caps { ev: set(&[EV_ABS]), abs: set(&[ABS_Z]), ..Default::default() };
    let pad = DeviceInfo { caps, ..Default::default() }.pad();
    let mut s = PadState::new(&pad);
    let info = AxisInfo { value: 32768, min: 0, max: 65535, flat: 0, fuzz: 0 };
    s.push(Ev::Abs { code: ABS_Z, value: 32768 });
    assert!(s.norm_centered(ABS_Z, info).abs() < 0.01, "mid-range rests centred");
    s.push(Ev::Abs { code: ABS_Z, value: 65535 });
    assert!((s.norm_centered(ABS_Z, info) - 1.0).abs() < 0.01);
    s.push(Ev::Abs { code: ABS_Z, value: 0 });
    assert!((s.norm_centered(ABS_Z, info) + 1.0).abs() < 0.01);
}

/// Every piece's box in unit space, captions included.
fn piece_boxes(l: &layout::Layout) -> Vec<(String, f32, f32, f32, f32)> {
    l.elements
        .iter()
        .map(|e| match e {
            layout::Piece::Button { label, x, y, r, .. } => (label.clone(), x - r, y - r, x + r, y + r),
            layout::Piece::Stick { cx, cy, r, .. } => ("stick".into(), cx - r, cy - r, cx + r, cy + r),
            layout::Piece::Hat { cx, cy, len, .. } => ("dpad".into(), cx - len / 2.0, cy - len / 2.0, cx + len / 2.0, cy + len / 2.0),
            // A caption is ~24 units tall, centred on label_y, as wide as the bar.
            layout::Piece::Bar { label, x, y, w, label_y, .. } => (label.clone(), *x, *y, x + w, label_y + 12.0),
            layout::Piece::Label { text, x, y, .. } => (text.clone(), x - 40.0, y - 12.0, x + 40.0, y + 12.0),
        })
        .collect()
}

/// The panel the user saw had the title over the bumpers, the bumpers off the
/// body, Sel/Guide/Start on top of the sticks and captions under the body.
/// Every piece must sit inside the body, with no two overlapping.
#[test]
fn test_layout_pieces_never_overlap() {
    for (name, caps) in [
        ("8bitdo", eightbitdo_caps()),
        ("synthetic", gamepad_caps()),
    ] {
        let boxes = piece_boxes(&layout::layout(&caps, Kind::Gamepad));
        for (label, x0, y0, x1, y1) in &boxes {
            assert!(
                *x0 >= 0.0 && *y0 >= 0.0 && *x1 <= layout::UNIT_W && *y1 <= layout::UNIT_H,
                "{name}: {label} leaves the body: ({x0},{y0})-({x1},{y1})"
            );
        }
        for (i, a) in boxes.iter().enumerate() {
            for b in &boxes[i + 1..] {
                let apart = a.3 <= b.1 || b.3 <= a.1 || a.4 <= b.2 || b.4 <= a.2;
                assert!(apart, "{name}: {} overlaps {}", a.0, b.0);
            }
        }
    }
}


#[test]
fn test_seed_sets_the_real_resting_value_without_logging() {
    let pad = DeviceInfo { caps: eightbitdo_caps(), ..Default::default() }.pad();
    let mut s = PadState::new(&pad);
    assert_eq!(s.axis(ABS_Z), Some(0), "a trigger starts released, not at 127");
    s.seed(ABS_Z, 3);
    s.seed(ABS_HAT0Y, -1);
    assert_eq!(s.axis(ABS_Z), Some(3));
    assert_eq!(s.hats[0].2, 0, "a held D-pad up at open is shown as up");
    assert_eq!(s.events, 0, "seeding is not an event");
}
