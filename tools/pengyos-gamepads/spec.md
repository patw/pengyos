# pengyos-gamepads — "Gamepads", see the controllers and test their controls

## Summary
A small libcosmic app for the Desktop that answers the two questions a game
controller raises and nothing in the Desktop answered: **which gamepads and
joysticks does this machine see**, and **do their buttons, sticks, triggers and
D-pad actually work**. It lists the game controllers with their kind, bus, vendor:product
and capability summary (every other input device is classified, and printed by
`--list`, but kept out of the window), and drives a live picture of the selected one: buttons
light up, stick knobs follow, trigger and pedal bars fill, the D-pad points, and
every event is printed as it arrives. A Rumble button sends a real force-feedback
effect. COSMIC Settings (epoch 1.9) has no page for any of this.

## Purpose
US-DSK-4 ("Play games") accepts that "GPU + audio + controllers are correct",
and the Desktop ships the controller drivers, `steam-devices` and GameMode for
it. But a controller is invisible until a game opens: nothing shows what is
attached, and nothing lets you check a stick calibration, a dead button or a
stuck trigger without launching Steam and finding a title with a pad setup
screen. COSMIC has no gamepad page; the Printer gap was closed the same way
(`pengyos-printers.desktop`), the disk gap with Disks.

## Deliverable
A Rust crate `pengyos-gamepads` (MIT), built like `pengyos-disks`:
- `lib.rs` — std only, `cargo test`-able with no display and no `/dev`: the
  device model, the classification (gamepad / joystick / flight stick / throttle
  quadrant / wheel / pedals / touch / mouse / keyboard / remote / keypad /
  audio jack / other), the axis and button names the Linux event codes map to,
  the sysfs capability-bitmask parser, the event decoder, the live
  button/axis/hat state with its calibration extremes, the axis normalisation,
  the hat-direction fold, the `--list` and `--json` formats, the rumble clamp.
- `layout.rs` — std only: the panel's geometry in a fixed 1000×560 unit space,
  computed from the device's capabilities, plus the scale into a canvas.
- `raw.rs` (feature `evdev`, libc) — the sysfs scan, the evdev ioctls
  (`EVIOCGBIT`, `EVIOCGABS`), the event reader and the rumble request
  (`EVIOCSFF`, an `EV_FF` write, `EVIOCRMFF`).
- `ioctl.rs` — std only: the evdev ioctl numbers and the `ff_effect` /
  `input_event` layouts, tested against the values `<linux/input.h>` produces.
- `app.rs` (feature `cosmic` = `evdev` + libcosmic) — the window: the device
  list, the canvas, the live values, the rumble controls, the event log.

## Interface
- `pengyos-gamepads` — the window.
- `pengyos-gamepads --list` — the devices, no window (works over ssh; reads
  sysfs only, so it answers even where the user may not open the nodes).
- `pengyos-gamepads --json` — the same as JSON, one object per device.
- `pengyos-gamepads --selftest` — the window driven by a synthetic complete
  gamepad: the app's own end-to-end check, with no hardware attached.

## Behaviour

**Enumeration.** Every `/dev/input/eventN`, read from
`/sys/class/input/eventN/device/{name,phys,modalias,capabilities/*}`. sysfs is
world-readable, so the list is complete for a user who cannot open the node;
the card then says so. Devices are deduped by name+phys (a combo device shows
once per interface), sorted by the `inputN` number, split into **Game
controllers** (the six controller kinds) and **Other input devices**.

**Classification** (`classify`, in this order — the specific shapes first,
because a mouse answers `ABS_X/ABS_Y` and a wheel answers `ABS_STEER`):

| Shape | Kind |
|---|---|
| `ABS_STEER` + `ABS_GAS`/`ABS_BRAKE` | Steering wheel |
| `ABS_GAS`/`ABS_BRAKE` (no steering) | Pedals |
| `ABS_THROTTLE`/`ABS_RUDDER` | Throttle quadrant |
| ≥1 stick pair + ≥3 game buttons + a game key | Gamepad |
| `ABS_X`+`ABS_Y` + ≥1 game button | Joystick |
| ≥1 stick pair + a hat pair | Flight stick |
| `ABS_MT_POSITION_X/Y` | Touch screen |
| `REL_X`+`REL_Y` + `BTN_MOUSE` | Mouse |
| `KEY_A` + ≥15 keys | Keyboard |
| `KEY_A` (few keys) | Keypad |
| switches only | Audio jack |
| switches + keys, no axes | Remote |
| only keys ≥ `BTN_MISC` | Remote (≥3 keys) / Keypad |
| nothing | Input device |

**Names.** Buttons are named as a pad's silkscreen names them (A/B/X/Y, Left
bumper, Left trigger, Select, Start, Guide, Left stick in); axes likewise (Left
stick X, Right trigger, Steering, Accelerator, Brake, Throttle, Rudder, D-pad
left/right). An unknown code is named `Axis`/`Button`, never blank.

**The panel.** A canvas drawn from the device's own capabilities: bumpers and
triggers on the top edge, D-pad and left stick on the left, the face buttons and
right stick on the right, Select/Guide/Start in the middle, extra axes as bars.
Nothing the device lacks is drawn, nothing it reports is left out: an axis with
no room in the shape becomes a bar, and every axis, switch, relative axis and
pressed button is also listed as text under the picture. A stick maps its own
`EVIOCGABS` range to -1..1; a trigger, pedal, throttle or wheel maps its
minimum..maximum to 0..1. The extremes seen since the pad was selected are kept,
so a calibration sweep is readable, and each axis prints its raw value.

**Live input.** One reader thread per selected device (an evdev read blocks),
reporting through a channel the UI drains on a 40 ms timer. `ENODEV` (unplugged)
drops the device from the list and says so. The list is re-scanned every 2 s, so
plugging a pad in is noticed without any action.

**Rumble.** Force feedback is an evdev interface: open the pad's own
`/dev/input/eventN` read-write (group `input`, 0660 — the same group the panel
needs to read it), upload a `FF_RUMBLE` effect with `EVIOCSFF` (the kernel
writes the slot id back), start it by writing an `EV_FF` `input_event` with
that id and value 1, stop it with value 0, and free the slot with `EVIOCRMFF`
(the id passed by value). The pad's driver supplies the motors through
`INPUT_FF_MEMLESS`, which every FF-capable pad driver selects; the joystick
node (`/dev/input/jsN`) has no force-feedback interface at all. A pad with no
motors gets the kernel's error as a sentence; the button is inert where the
device reports no `FF_RUMBLE`.

**Sysfs bitmaps** are printed as kernel `unsigned long` words: 64 bits on
x86_64, most significant word first, leading zeros dropped.

**Permissions.** Reading `/dev/input/eventN` needs the `input` group, which
systemd's udev rules give the devices (mode 0660). The image defines `input`
(`board/desktop/users.table`) and the installer puts the owner in it
(`USER_GROUPS`); `merge-accounts` adds existing owners once. A user who is not a
member gets the list (from sysfs) plus the sentence that names the group — the
app never runs as root and adds no polkit rule.

## Non-goals
Remapping, deadzone tuning, per-game profiles, calibration storage, Bluetooth
pairing, and Steam Input configuration: this reports and tests, it does not
transform. Internal input devices (a laptop keyboard) are listed, not hidden —
the list is the machine's truth — but only controllers headline it.

## Acceptance
- `cargo test` green with no display and no `/dev` access.
- `--list` on a real machine lists every event node with the right kind, and
  says `no game controllers` when there are none (measured on behoserver: 18
  nodes, 0 controllers, the audio jacks as Audio, the power buttons as Other).
- `--json` parses as JSON; an empty list is `[]`.
- The panel follows a real pad: buttons, both sticks, triggers, D-pad; the log
  prints each event with its raw value and the extremes seen.
- `--selftest` animates a complete synthetic pad (the panel is exercised with
  no hardware, which is how it is checked under QEMU).
- In the image: installed by `board/desktop/post-build.sh`, launched by
  `io.catbee.pengyos.Gamepads.desktop`, built by `scripts/build-cosmic.sh`.
