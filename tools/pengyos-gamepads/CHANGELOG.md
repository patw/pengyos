# Changelog — pengyos-gamepads

## [0.1.0] — 2026-10-12

### Added
- The Gamepads app (libcosmic, over evdev + sysfs): list every attached input
  device with its kind, bus, `vendor:product` and capability summary, headline
  the game controllers, and drive a live picture of the selected one — buttons
  light up, stick knobs follow, trigger/pedal/wheel bars fill, the D-pad
  points, each axis prints its raw value and the extremes seen, every event is
  logged. A Rumble button sends a real `FF_RUMBLE` effect to the pad's
  `/dev/input/js` node.
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
