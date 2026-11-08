# Changelog

## 0.3.0

- A **Fans** view in the window and the terminal view, over `pengyos-fans status
  --json`: each fan with its speed, duty and who runs it (the chip's curve, the
  service's software curve, or left alone). In the window, profile buttons and
  Detect run `pkexec /usr/bin/pengyos-fans ...` (an administrator's password; no
  polkit rule grants it). The terminal view shows the `sudo` commands instead and
  never changes a fan. `--view fans` starts the window there. `PENGYOS_FANS`
  names a stand-in for `pengyos-fans status` (tests).
- `serde_json` is now a direct dependency (it was already in the tree through
  hwall-app).

## 0.2.0

For PengyOS 26.11.9: the Server's terminal view, and kernel limits that survive
lm-sensors.

- Terminal view (`--features tui`, ratatui): the Sensors table with Value / Min /
  Max and the same amber/red limit colouring, the Hardware inventory, a name
  filter, pause, min/max reset and sampling interval. Built for the Linux
  console (16 colours, 80x25) as well as ssh. The Server image ships it as
  `pengyos-hwmon`; the Desktop binary carries it too (`--tui`, and it starts
  there by itself when there is no display, e.g. over ssh). It reads the
  Desktop app's saved aliases and alert rules but never writes them.
- `--check` prints every sensor grouped by device, with each device's limit and
  where it came from, instead of the first eight rows.
- Fixed: on a real PengyOS install no limit ever came from the kernel. hwall-core
  drops min/max/crit from sensors whose value lm-sensors supplied, and PengyOS
  ships lm-sensors, so every temperature fell back to the table. Celsius limits
  are now read from sysfs next to the sensor's `tempN_input` when that happens.
- Fixed: a temperature limit outside 20..150 °C is ignored. NVMe reports "no
  threshold" as 65261.85 °C on its extra sensors, which became the drive's limit.
- The device limit prefers a kernel limit over any table default, then the
  highest, and is one function shared by every front-end, so they always name the
  same source.

## 0.1.0

First version, for PengyOS Desktop 26.11.9.

- COSMIC front-end over HWall's collectors: Sensors / Hardware / Limits panes.
- Dense table with Value / Min / Max, monospace right-aligned numerals, row
  virtualisation and GPU rendering (wgpu): fast scrolling costs 0.8% of a core
  instead of 57.7%.
- TJ Max policy with provenance: kernel `*_crit` / `*_max` first, then a CPU
  family table (AMD 23/25/26 -> 95 C, Intel family 6 -> 100 C, GPU 95 C,
  storage 80 C), with the resolved limit and its reason shown in the device
  header.
- Per-sensor warning/critical editor writing the hwall-app alert rules, which
  also colour the main table.
- `--check`: one collection pass as text, with or without the GUI, for ssh and
  for the Server TUI that will follow.
