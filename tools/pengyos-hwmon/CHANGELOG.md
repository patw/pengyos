# Changelog

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
