# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.7.0] - 2026-11-27

### Added
- **An ISO library**, `~/vms/isos`: `pengyos-vm iso list [--json] | add FILE|URL
  [--name N] [--progress] | remove NAME`. An installer ISO is kept once and
  attached to any number of VMs, and `create --iso` / `set --iso` take a library
  name (with or without `.iso`) as well as a path. `add` hard-links a file on the
  same filesystem (no second copy), downloads a URL, checks the free space
  first, and refuses anything without an ISO 9660 volume (an HTML error page
  saved from a URL). It writes a hidden `.part` file and renames it only when
  complete. `--progress` prints JSON lines for the front ends. `list` shows
  which VMs use each one, and `remove` refuses an ISO still attached (it names
  the `set VM --eject` to run). `isos` and `images` are no longer allowed as VM
  names.

### Fixed
- A download that fails (404, no network) is now one line, `URL: the server
  answered 404 Not Found`, not a Python traceback. That covers `create --image
  URL` too.

## [0.6.0] - 2026-11-27

### Added
- **Disk snapshots**, for "try something, roll back, try it another way":
  `pengyos-vm snapshot NAME create [LABEL] | list [--json] | revert SNAP
  [--yes] | delete SNAP`. They are internal qcow2 snapshots of `disk.qcow2`,
  named s1, s2, ... and disk state only, no memory. On a running VM, QEMU takes
  one live (QMP `blockdev-snapshot-internal-sync`); it is the disk as a power cut
  would leave it, and `create` says so. A stopped VM's snapshot is clean.
  `revert` asks (or `--yes`), switches a running VM off at once (its disk is
  being replaced, so a shutdown would be wasted), rolls back, puts back the UEFI
  variables saved with the snapshot (`snapshots/SNAP.vars.fd`), and starts it
  again. `delete` works running or stopped. `list` shows when each was taken and
  whether the VM was running; `show` and `show --json` include them.
- Tested on behothinkpad with a PengyOS Core guest: a file written before a live
  snapshot and changed after it read the old contents again after `revert`.

## [0.5.0] - 2026-11-27

### Security
- **A VM's screen is no longer open to every account on the machine.** It was
  VNC on `127.0.0.1:59xx` with no password, so any local user (another login, a
  service account, a container with host networking) could watch and type into
  anyone's VM. Now QEMU serves it only on `~/vms/NAME/vnc.sock`, and `~/vms` and
  each VM folder are 0700. `start` fixes the mode of folders made by older
  versions, and the serial console and QMP sockets are covered by the same lock.
  There is no TCP listener at all. From another machine (or for a viewer that
  only speaks TCP), SSH forwards a port to the socket:
  `ssh -N -L 5901:/home/you/vms/NAME/vnc.sock you@host`. `pengyos-vm vnc NAME`
  prints that line.
- `show --json` (and `list --json`): `vnc` (the TCP address) is replaced by
  `vnc_socket`. pengy-vmm's Screen row shows the socket and the ssh line.
- A VM that is running when the image updates keeps its TCP port until it is
  next started.

## [0.4.0] - 2026-11-18

### Changed
- `pengyos-vm run` is offline by default: the VM's NAT gets QEMU's `restrict=on`,
  so a throwaway job reaches neither the internet nor this machine. Before, QEMU's
  NAT mapped 10.0.2.2 onto the host's 127.0.0.1, so a job could talk to services
  that listen only there (pengy-vmm, a local model server). `--online` gives the
  old behaviour for jobs that install packages or fetch data; a failed offline
  run says so. `list` shows these VMs' network as `offline`. `create` is unchanged.

## [0.3.1] - 2026-11-14

### Fixed
- `bridge enable` writes `UseDomains=yes` and `MulticastDNS=yes` on the bridge,
  as the image's wired `.network` file has. The bridge file takes over the port,
  so a bridged Server lost its DHCP search domain (bare LAN names stopped
  resolving) and resolved's mDNS. A bridge enabled before this keeps the old
  file: `sudo pengyos-vm bridge disable && sudo pengyos-vm bridge enable <port>`.

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
