# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.3.0] - 2026-10-31

### Added
- `pengyos-vm run`: a throwaway VM for one command. A copy-on-write overlay of
  a cached image (`--image`, default `$PENGYOS_VM_IMAGE`), a per-run SSH key,
  `--copy` in and `--fetch` out of `/root/run`, `--timeout`, then the VM is
  deleted (Ctrl-C and SIGTERM too; the next run sweeps one that was killed).
  `--keep` keeps it. The exit status is the command's.

### Changed
- `pengyos-vm ssh` uses a VM's own key when it has one (a kept `run`).
- `create --image` grows the disk to 20G when `--disk` is not given (a 1 GB
  Core image left 362 MB free), and `create`/`set --forward` say to open the
  port inside a guest with a firewall (a PengyOS guest is default-deny).
- When the user manager is going down (shutdown, reboot, last logout without
  linger) a VM gets 30 s to shut down, not 100 s, and `qemu.log` says it was
  asked to. `pengyos-vm-shutdown-notice.service` says on the console which VMs a
  shutdown is waiting for.

## [0.2.0] - 2026-10-31

### Added
- `pengy-vmm`: the VMs in a browser (127.0.0.1:5100, per-user token, user
  unit `pengy-vmm.service`), every action a `pengyos-vm` command.

### Changed
- Linger needs no sudo now (polkit `60-pengyos-linger.rules`): `start` says
  `loginctl enable-linger`, and `autostart on` turns it on.

## [0.1.0] - 2026-10-31

### Added
- `pengyos-vm`, shipped in the Server overlay, so the Server and the Desktop
  have it: `list`, `create`, `start`, `stop`, `console`, `ssh`, `vnc`, `log`,
  `show`, `set`, `autostart`, `delete`, and `bridge status|enable|disable`.
- VMs run as the user under `pengyos-vm@NAME.service` (systemd user manager).
- NAT with port forwards (Wi-Fi included) or a LAN bridge (wired only) through
  the setuid `qemu-bridge-helper`, limited to `pengyos-br0`.
- `--ssh-key` and the VM name go to systemd guests as credentials.
