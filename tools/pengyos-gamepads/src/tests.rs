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
        abs: set(&[ABS_STEER, ABS_GAS, ABS_BRAKE, ABS_X, ABS_Y, ABS_Z, ABS_RZ]),
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
    // The kernel prints the highest word first: BTN_A (0x130) is bit 304, i.e.
    // word 9, bit 16 -> the second word from the right of a 10-word key mask.
    let caps = Caps { ev: set(&[EV_KEY]), keys: set(&[BTN_A]), ..Default::default() };
    let text = format_caps_sysfs(&caps);
    let line = text.lines().find(|l| l.starts_with("key ")).unwrap();
    let words: Vec<&str> = line.split_whitespace().skip(1).collect();
    assert_eq!(words.len(), 10);
    // Highest word first, so word 9 (the one holding bit 304) is printed first.
    assert_eq!(words[0], "00010000");
    assert_eq!(words[9], "00000000");
    assert_eq!(caps_from_sysfs(&text).keys, set(&[BTN_A]));
}

#[test]
fn test_parse_caps_words_zero() {
    assert!(parse_caps_words("0").is_empty());
    assert!(parse_caps_words("").is_empty());
    assert_eq!(parse_caps_words("00000000 00000001"), set(&[0]));
    assert_eq!(parse_caps_words("00000001 00000000"), set(&[32]));
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
    assert_eq!(state.axis(ABS_Z), Some(127));
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
    for code in [BTN_A, BTN_B, BTN_X, BTN_Y, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR] {
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
    assert_eq!(l.readout.len(), 4);
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
    wheel.abs.insert(ABS_STEER);
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
    assert!(bars.contains(&ABS_STEER), "{bars:?}");
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
    assert_eq!(axis_name(ABS_STEER), "Steering");
    assert_eq!(Kind::Gamepad.title(), "Gamepad");
    // An unknown code is named, never blank.
    assert_eq!(axis_name(0x2ff), "Axis");
    assert_eq!(button_name(0x2ff), "Button");
}

#[test]
fn test_rumble_level_is_clamped() {
    assert_eq!(rumble_level(-5), 0);
    assert_eq!(rumble_level(40000), 32767);
    assert_eq!(rumble_level(1000), 1000);
}

#[test]
fn test_gone_message() {
    assert!(Pad::is_gone_message("/dev/input/js0: No such device"));
    assert!(!Pad::is_gone_message("/dev/input/js0: Permission denied"));
}
