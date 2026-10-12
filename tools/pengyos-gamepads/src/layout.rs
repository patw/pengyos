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
    /// An analog stick: the ring, and the knob at its current value. `press`
    /// is the stick-click button (L3/R3): it lights the ring, so the click
    /// needs no separate shape on top of the stick.
    Stick { x: u16, y: u16, cx: f32, cy: f32, r: f32, press: Option<u16> },
    /// A D-pad drawn as a cross whose pressed arm lights up (`(abs_x, abs_y)`).
    Hat { x: u16, y: u16, cx: f32, cy: f32, arm: f32, len: f32 },
    /// A bar filled from the axis's minimum (triggers, pedals, steering).
    /// `label_y` is where its caption goes, in unit space.
    Bar { code: u16, x: f32, y: f32, w: f32, h: f32, label: String, label_y: f32 },
    /// Text that is not part of an element (captions).
    Label { x: f32, y: f32, text: String, size: f32 },
}

/// The panel for one device.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Layout {
    pub elements: Vec<Piece>,
}

/// Place every capability the device reports in the unit space.
///
/// The controller shape follows an Xbox pad, and every element stays inside
/// the 1000×560 body with room around it (`test_layout_pieces_never_overlap`):
///
/// ```text
///   [ LT bar ]                         [ RT bar ]        y  30..55
///      (LB)                               (RB)           y 105
///   ( L stick )   Sel Guide Start     Y                  y 200..280
///                                   X   B
///                                     A
///          [D-pad]        ( R stick )                    y 420
/// ```
/// Values are not printed in the picture: the "Live values" list under it
/// carries every axis by name, so the drawing stays uncluttered.
pub fn layout(caps: &Caps, kind: Kind) -> Layout {
    let mut l = Layout::default();

    // --- top edge: triggers, then bumpers ----------------------------------
    let (lt, rt) = caps.triggers();
    for (code, x) in [(lt, 240.0), (rt, 760.0)] {
        if let Some(code) = code {
            l.elements.push(Piece::Bar {
                code,
                x: x - 100.0,
                y: 30.0,
                w: 200.0,
                h: 24.0,
                label: axis_name(code).into(),
                label_y: 72.0,
            });
        }
    }
    // Digital triggers (no analog axis): buttons where the bars would be.
    for (code, x, analog) in [(crate::BTN_TL2, 240.0, lt.is_some()), (crate::BTN_TR2, 760.0, rt.is_some())] {
        if caps.has_key(code) && !analog {
            let label = format!("{}T", crate::button_name(code).chars().next().unwrap_or('L'));
            l.elements.push(Piece::Button { code, x, y: 42.0, r: 26.0, label });
        }
    }
    for (code, x, label) in [(crate::BTN_TL, 240.0, "LB"), (crate::BTN_TR, 760.0, "RB")] {
        if caps.has_key(code) {
            l.elements.push(Piece::Button { code, x, y: 118.0, r: 30.0, label: label.into() });
        }
    }

    // --- sticks -----------------------------------------------------------
    let thumb = |code: u16| caps.has_key(code).then_some(code);
    if caps.has_abs(crate::ABS_X) && caps.has_abs(crate::ABS_Y) {
        l.elements.push(Piece::Stick {
            x: crate::ABS_X,
            y: crate::ABS_Y,
            cx: 240.0,
            cy: 260.0,
            r: 72.0,
            press: thumb(crate::BTN_THUMBL),
        });
    }
    if let Some((rx, ry)) = caps.right_stick() {
        l.elements.push(Piece::Stick { x: rx, y: ry, cx: 620.0, cy: 440.0, r: 72.0, press: thumb(crate::BTN_THUMBR) });
    }

    // --- D-pad ------------------------------------------------------------
    if let Some((x, y)) = caps.hats().first() {
        l.elements.push(Piece::Hat { x: *x, y: *y, cx: 380.0, cy: 440.0, arm: 40.0, len: 120.0 });
    }

    // --- face buttons -----------------------------------------------------
    for (code, x, y) in [
        (crate::BTN_Y, 760.0, 200.0),
        (crate::BTN_X, 690.0, 270.0),
        (crate::BTN_B, 830.0, 270.0),
        (crate::BTN_A, 760.0, 340.0),
        (crate::BTN_C, 830.0, 190.0),
        (crate::BTN_Z, 690.0, 350.0),
    ] {
        if caps.has_key(code) {
            let label = crate::button_name(code).chars().next_back().unwrap_or('?').to_string();
            l.elements.push(Piece::Button { code, x, y, r: 30.0, label });
        }
    }

    // --- centre: Select / Guide / Start -----------------------------------
    for (code, x, label) in [
        (crate::BTN_SELECT, 430.0, "Sel"),
        (crate::BTN_MODE, 500.0, "Guide"),
        (crate::BTN_START, 570.0, "Start"),
    ] {
        if caps.has_key(code) {
            l.elements.push(Piece::Button { code, x, y: 220.0, r: 28.0, label: label.into() });
        }
    }

    // --- the axes the shape has no room for --------------------------------
    // Steering, pedals, throttles, a joystick's extra axes: a stacked column
    // of bars on the right edge, clear of the face buttons and right stick.
    let placed: Vec<u16> = l
        .elements
        .iter()
        .flat_map(|e| match e {
            Piece::Bar { code, .. } => vec![*code],
            Piece::Stick { x, y, .. } | Piece::Hat { x, y, .. } => vec![*x, *y],
            _ => vec![],
        })
        .collect();
    let mut y = 410.0f32;
    for code in caps.shown_axes() {
        if placed.contains(&code) || caps.hats().iter().any(|(hx, hy)| *hx == code || *hy == code) {
            continue;
        }
        if y > 520.0 {
            break; // the rest are listed by name under the picture
        }
        l.elements.push(Piece::Bar {
            code,
            x: 760.0,
            y,
            w: 200.0,
            h: 18.0,
            label: axis_name(code).into(),
            label_y: y + 30.0,
        });
        y += 56.0;
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
