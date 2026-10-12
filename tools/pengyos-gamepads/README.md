# pengyos-gamepads — "Gamepads"

See the gamepads and joysticks the machine has attached, and test their
controls. COSMIC Settings (epoch 1.9) has no page for controllers: until now a
pad was invisible until a game opened it, and checking a stick, a dead button
or a stuck trigger meant launching Steam and finding a pad setup screen.

- **The list**: every `/dev/input` device with its kind (gamepad, joystick,
  flight stick, throttle quadrant, steering wheel, pedals, touch screen, mouse,
  keyboard, remote, keypad, audio jack), how it is attached (USB, Bluetooth,
  ACPI…), its `vendor:product`, and a capability summary
  (`2 sticks, 12 buttons, D-pad, 2 trigger axes, rumble`). Controllers headline
  the list; everything else is listed under it.
- **The test panel**: a picture drawn from the device's own capabilities —
  buttons light up, stick knobs follow the stick, trigger and pedal bars fill,
  the D-pad points. Each axis prints its raw value and the extremes seen since
  you selected the pad, so a calibration sweep is readable. Every event is also
  printed as text, newest first.
- **Rumble**: a strength slider and a Rumble button, sent as a real
  force-feedback effect to the pad's event node.

Plugging a pad in is noticed by itself (the list is re-scanned every two
seconds); unplugging one says so and drops it.

## How it works

Enumeration reads **sysfs** (`/sys/class/input/*/device/…`), which is
world-readable, so the list is complete even for a user who may not open the
device node — the card then says exactly that, and names the `input` group.
The live panel reads the evdev node itself, one reader thread per selected
device (an evdev read blocks), drained by the UI on a 40 ms timer. Rumble uploads an
`FF_RUMBLE` effect to the pad's own event node (`EVIOCSFF`), starts it by
writing an `EV_FF` event, and erases it (`EVIOCRMFF`). The app never
runs as root and adds no polkit rule.

- `src/lib.rs`, `src/layout.rs` — std only: the device model, the
  classification, the axis/button names, the sysfs bitmask parser, the event
  decoder, the live state and its calibration extremes, the panel geometry, the
  output formats.
- `src/raw.rs` (feature `evdev`, zbus-free, libc only) — the sysfs scan, the
  ioctls, the event reader, the js lookup and the rumble request.
- `src/app.rs` (feature `cosmic`) — the window.

## Use

```console
pengyos-gamepads              # the window
pengyos-gamepads --list       # the devices, no window (works over ssh)
pengyos-gamepads --json       # the same as JSON
pengyos-gamepads --selftest   # the window, driven by a synthetic pad
```

## Tested

Unit: 21 tests, no display and no `/dev` — the classification of eleven device
shapes (a mouse must never read as a joystick), the sysfs capability-bitmask
round trip and its word order, the event decode/encode round trip, the live
state (buttons, axes, ranges, the D-pad folded from both of its axes), axis
normalisation over the device's own range, the panel layout (every button the
pad has is placed, nothing it lacks is drawn, an axis with no room becomes a
bar), the modalias/bus parsing, the list and JSON formats, the layout scaling.

Live: `--list` on a real machine (behoserver, 18 event nodes, no controller
attached) — the audio jacks classify as Audio, the power buttons as Other, the
WMI hotkeys as Other, and the headline line reads `no game controllers`.

## Build

```console
cargo test                                   # the decision logic: no display, no /dev
cargo run --features evdev -- --list         # against the host's /dev/input
CARGO_TARGET_DIR=~/cosmic-build/install-gui cargo build --release --features cosmic
```

`scripts/build-cosmic.sh pengyos-gamepads` does the last one; the Desktop
post-build installs `~/cosmic-build/install-gui/release/pengyos-gamepads`, and
the overlay carries `io.catbee.pengyos.Gamepads.desktop`.
