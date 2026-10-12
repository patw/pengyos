//! The test panel's picture: where each button, stick and bar of the drawn
//! gamepad goes.
//!
//! Pure and std-only on purpose — the coordinates are computed in a fixed
//! 1000×560 unit space by [`layout`], the canvas scales that space into its own
//! bounds, and both steps are unit-tested without a display. A device answers
//! with the elements it has: a wheel gets its steering bar, a flight stick its
//! hats, a remote its key buttons, so nothing is drawn as something it does not
//! report.

use crate::axis_name;
use crate::{Caps, Kind};

/// The unit space the layout is computed in.
pub const UNIT_W: f32 = 1000.0;
pub const UNIT_H: f32 = 560.0;

#[derive(Clone, Debug, PartialEq)]
pub enum Piece {
    /// A button, drawn as a rounded square with its label inside.
    Button { code: u16, x: f32, y: f32, r: f32, label: String },
    /// An analog stick: the ring, and the knob at its current value.
    Stick { x: u16, y: u16, cx: f32, cy: f32, r: f32 },
    /// A D-pad drawn as a cross whose arms light up (`(abs_x, abs_y)`).
    Hat { x: u16, y: u16, cx: f32, cy: f32, arm: f32, len: f32 },
    /// A bar filled from the axis's minimum (triggers, pedals, steering).
    /// `label_y` is where its caption goes, in unit space: under the trigger
    /// bars (the bumpers are above them) and under the spare-axis bars.
    Bar { code: u16, x: f32, y: f32, w: f32, h: f32, label: String, label_y: f32 },
    /// Text that is not part of an element (values, captions).
    Label { x: f32, y: f32, text: String, size: f32 },
}

/// The panel for one device.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Layout {
    pub elements: Vec<Piece>,
    /// Axes whose value is printed as a number, at the given unit position.
    pub readout: Vec<(u16, f32, f32)>,
}

/// Place every capability the device reports in the unit space.
pub fn layout(caps: &Caps, kind: Kind) -> Layout {
    let mut l = Layout::default();

    // --- top edge: bumpers, extra bumpers, triggers ------------------------
    for (code, x) in [(crate::BTN_TL, 250.0), (crate::BTN_TR, 750.0)] {
        if caps.has_key(code) {
            let label = format!("{}B", crate::button_name(code).chars().next().unwrap_or('L'));
            l.elements.push(Piece::Button { code, x, y: 44.0, r: 52.0, label });
        }
    }
    for (code, x) in [(crate::BTN_TL2, 120.0), (crate::BTN_TR2, 880.0)] {
        if caps.has_key(code) {
            let label = format!("{}2", crate::button_name(code).chars().next().unwrap_or('L'));
            l.elements.push(Piece::Button { code, x, y: 44.0, r: 44.0, label });
        }
    }
    for (code, x) in [(crate::ABS_Z, 250.0), (crate::ABS_RZ, 750.0)] {
        if caps.has_abs(code) {
            l.elements.push(Piece::Bar {
                code,
                x: x - 105.0,
                y: 112.0,
                w: 210.0,
                h: 22.0,
                label: axis_name(code).into(),
                label_y: 152.0,
            });
        }
    }

    // --- left cluster: D-pad, then the left stick -------------------------
    if let Some((x, y)) = caps.hats().first() {
        l.elements.push(Piece::Hat { x: *x, y: *y, cx: 210.0, cy: 300.0, arm: 56.0, len: 150.0 });
        // The D-pad's caption goes under the body, where nothing is drawn.
        l.elements.push(Piece::Label { x: 210.0, y: 548.0, text: "D-pad".into(), size: 19.0 });
    }
    if caps.has_abs(crate::ABS_X) && caps.has_abs(crate::ABS_Y) {
        l.elements.push(Piece::Stick { x: crate::ABS_X, y: crate::ABS_Y, cx: 392.0, cy: 386.0, r: 84.0 });
        l.readout.push((crate::ABS_X, 392.0, 508.0));
        l.readout.push((crate::ABS_Y, 392.0, 538.0));
    }

    // --- right cluster: the face buttons, then the right stick ------------
    for (code, x, y) in [
        (crate::BTN_Y, 790.0, 216.0),
        (crate::BTN_X, 722.0, 284.0),
        (crate::BTN_B, 858.0, 284.0),
        (crate::BTN_A, 790.0, 352.0),
        (crate::BTN_C, 654.0, 216.0),
        (crate::BTN_Z, 926.0, 352.0),
    ] {
        if caps.has_key(code) {
            let label = crate::button_name(code).chars().next_back().unwrap_or('?').to_string();
            l.elements.push(Piece::Button { code, x, y, r: 34.0, label });
        }
    }
    if caps.has_abs(crate::ABS_RX) && caps.has_abs(crate::ABS_RY) {
        l.elements.push(Piece::Stick { x: crate::ABS_RX, y: crate::ABS_RY, cx: 608.0, cy: 386.0, r: 84.0 });
        l.readout.push((crate::ABS_RX, 608.0, 508.0));
        l.readout.push((crate::ABS_RY, 608.0, 538.0));
    }

    // --- centre: stick-in buttons, then Select / Guide / Start ------------
    for (code, x) in [(crate::BTN_THUMBL, 392.0), (crate::BTN_THUMBR, 608.0)] {
        if caps.has_key(code) {
            l.elements.push(Piece::Button {
                code,
                x,
                y: 470.0,
                r: 26.0,
                label: crate::button_name(code).chars().next().unwrap_or('?').to_string(),
            });
        }
    }
    for (code, x) in [
        (crate::BTN_SELECT, 440.0),
        (crate::BTN_MODE, 500.0),
        (crate::BTN_START, 560.0),
    ] {
        if caps.has_key(code) {
            let label = match code {
                crate::BTN_SELECT => "Sel",
                crate::BTN_MODE => "Guide",
                _ => "Start",
            };
            l.elements.push(Piece::Button { code, x, y: 300.0, r: 28.0, label: label.into() });
        }
    }

    // --- the axes the gamepad shape has no room for ------------------------
    // Steering, pedals, throttles, extra hats and any axis the device reports
    // that the shape above does not place: stacked bars in the middle.
    let mut extra: Vec<u16> = Vec::new();
    for code in caps.shown_axes() {
        let used = caps.hats().iter().any(|(x, _)| *x == code)
            || matches!(code, crate::ABS_X | crate::ABS_Y | crate::ABS_RX | crate::ABS_RY
                | crate::ABS_Z | crate::ABS_RZ);
        if !used {
            extra.push(code);
        }
    }
    let mut y = 150.0f32;
    for code in extra {
        l.elements.push(Piece::Bar {
            code,
            x: 430.0,
            y,
            w: 140.0,
            h: 20.0,
            label: axis_name(code).into(),
            label_y: y + 34.0,
        });
        y += 42.0;
    }

    // --- a remote / keypad / mouse: draw the keys it has ------------------
    if matches!(kind, Kind::Remote | Kind::Keypad | Kind::Mouse | Kind::Keyboard | Kind::Other) {
        let mut x = 150.0f32;
        for code in caps.keys.iter().filter(|k| **k >= crate::BTN_MISC).take(10) {
            l.elements.push(Piece::Button {
                code: *code,
                x,
                y: 300.0,
                r: 34.0,
                label: crate::button_name(*code).chars().next().unwrap_or('?').to_string(),
            });
            x += 90.0;
        }
        let mut x = 150.0f32;
        for code in caps.keys.iter().filter(|k| **k < crate::BTN_MISC).take(10) {
            l.elements.push(Piece::Button {
                code: *code,
                x,
                y: 400.0,
                r: 34.0,
                label: crate::key_label(*code),
            });
            x += 90.0;
        }
        for (code, label) in [(crate::REL_X, "relative X"), (crate::REL_Y, "relative Y")] {
            if caps.has_rel(code) {
                l.elements.push(Piece::Bar {
                    code,
                    x: 620.0,
                    y: 200.0,
                    w: 200.0,
                    h: 20.0,
                    label: label.into(),
                    label_y: 232.0,
                });
            }
        }
    }

    l
}

/// Scale a unit-space point into a canvas's bounds, keeping the aspect ratio.
pub fn scale(x: f32, y: f32, w: f32, h: f32) -> (f32, f32) {
    let s = (w / UNIT_W).min(h / UNIT_H);
    (
        (w - UNIT_W * s) / 2.0 + x * s,
        (h - UNIT_H * s) / 2.0 + y * s,
    )
}

/// Scale a length (a radius, a width) into the canvas's units.
pub fn scale_len(v: f32, w: f32, h: f32) -> f32 {
    (w / UNIT_W).min(h / UNIT_H) * v
}
