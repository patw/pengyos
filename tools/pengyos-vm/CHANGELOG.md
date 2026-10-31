# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.0] - 2026-10-31

### Added
- `pengyos-vm`, shipped in the Server overlay, so the Server and the Desktop
  have it: `list`, `create`, `start`, `stop`, `console`, `ssh`, `vnc`, `log`,
  `show`, `set`, `autostart`, `delete`, and `bridge status|enable|disable`.
- VMs run as the user under `pengyos-vm@NAME.service` (systemd user manager).
- NAT with port forwards (Wi-Fi included) or a LAN bridge (wired only) through
  the setuid `qemu-bridge-helper`, limited to `pengyos-br0`.
- `--ssh-key` and the VM name go to systemd guests as credentials.
