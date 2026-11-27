# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-27

### Added
- **Dev boxes** (distrobox) and **Containers** (podman, Quadlet services, images):
  see spec.md. Keep running at boot rewrites a container as a Quadlet service and
  rolls back (file removed, container renamed back and restarted) when the service
  does not start, quoting the service's journal. `keep-at-boot NAME` on the command
  line does the same. Tested on behobasement: nginx kept its port and environment
  (not the image's own), and a port clash rolled back cleanly.
- `--screen NAME`, `--page boxes|containers`.
- The screen draws through an allocated image (iced 0.14 uploads new images
  asynchronously, which made it flicker or stay black), and SSH waits for the
  guest's SSH greeting before opening a terminal.
- The window, step 2 of spec.md: **virtual machines**. A list of your VMs; for
  each, Start, Shut down, Force off (asks), SSH in a terminal, Eject ISO, Delete
  (asks), start at boot, its settings, and **disk snapshots** (take with a label,
  roll back and delete, both ask). **New VM** picks an installer ISO from the
  library (or adds one with a file chooser) or a disk image file or URL, with
  CPUs, memory, disk, bridge, forwards, SSH key and BIOS, and shows the
  `pengyos-vm create` line it will run. Every result shows the command that ran.
- **The screen**, built in: an RFB 3.8 client of our own (security None, Raw,
  DesktopSize, QEMU extended key events) over the VM's private `vnc.sock`,
  drawn on a canvas scaled to fit or 1:1. Keys are sent by physical position
  (scancodes), so the guest's layout decides what they type; the first click
  gives the screen the keyboard and a click outside gives it back. Ctrl+Alt+Del
  and Reconnect buttons.
- `pengyos-machines --list [--json]`: the VMs and ISOs, no window.
