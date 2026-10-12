# Changelog — pengyos-gamepads

## [0.1.1] — 2026-10-12

### Fixed
- **Rumble works at all.** 0.1.0 sent its effect to the joystick node
  (`/dev/input/jsN`, which has no force-feedback interface), with ioctl numbers
  whose direction bits were swapped and whose bases were wrong (`EVIOCSFF` at
  `0x30`, `EVIOCRMFF` at `0x31`; the header says `0x80`/`0x81`), with a struct
  that was not `struct ff_effect` (28 bytes, not 48), a device path handed to
  `open(2)` without a terminating NUL, and it never started the effect. Rumble
  now uploads to the pad's event node, starts and stops the effect with `EV_FF`
  writes, and erases it with the id by value. The kernel fragment's
  `CONFIG_INPUT_FF_MEMSET` is gone: no such symbol exists (kconfig dropped it
  silently); the pad drivers select `INPUT_FF_MEMLESS`.
- **Axis ranges are the device's own.** `EVIOCGABS` was built at `0x26 + abs`
  (the header says `0x40 + abs`), so every range query failed and the panel fell
  back to defaults. `EVIOCGBIT` now asks for the full key bitmap (96 bytes, up
  to `KEY_MAX`) and accepts its byte-count answer.
- **A mouse no longer lists as a keyboard.** Sysfs prints capability bitmaps
  in 64-bit words on x86_64; they were parsed as 32-bit words, so every code
  above bit 31 landed at the wrong index (a Logitech G305 lost `BTN_LEFT` and
  was listed as `[Keyboard]`, a keyboard's mouse interface as `Input device`).
- The ioctl numbers and the `ff_effect`/`input_event` layouts now live in the
  std-only `ioctl.rs` and are tested against the values `<linux/input.h>`
  produces, plus a test on the G305's real sysfs text (25 tests).

## [0.1.0] — 2026-10-12

### Added
- The Gamepads app (libcosmic, over evdev + sysfs): list every attached input
  device with its kind, bus, `vendor:product` and capability summary, headline
  the game controllers, and drive a live picture of the selected one — buttons
  light up, stick knobs follow, trigger/pedal/wheel bars fill, the D-pad
  points, each axis prints its raw value and the extremes seen, every event is
  logged. A Rumble button sends a `FF_RUMBLE` effect (broken in this
  version; see 0.1.1).
- Classification of eleven device shapes (gamepad, joystick, flight stick,
  throttle quadrant, steering wheel, pedals, touch screen, mouse, keyboard,
  remote, keypad, audio jack) from the capability sets alone, so a mouse never
  reads as a joystick and a wheel is not drawn as a gamepad.
- `--list` and `--json` (no window, sysfs only, so they answer over ssh and for
  a user who may not open the nodes), and `--selftest`, which animates a
  complete synthetic gamepad so the panel is exercised with no hardware.
- 21 unit tests covering the classification, the sysfs capability-bitmask
  parser (round trip + word order), the event decoder, the live state and axis
  normalisation over the device's own `EVIOCGABS` range, the D-pad fold from
  both of its axes, the panel layout, the modalias/bus parsing, and the output
  formats.
