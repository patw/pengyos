# pengyos-machines — "Machines", the Desktop's VM and container app

Status: **spec, not built** (2026-11-27). Decided with the operator: the name
*Machines*, a VNC view built into the app, Quadlet services in v1.

## Summary
A libcosmic app for the Desktop (App Library, System folder) that creates and
manages **virtual machines, dev boxes and containers** for the user who runs it.
It is a window over the command-line tools the image already ships —
`pengyos-vm`, `distrobox`, `podman` and Quadlet — and does nothing they cannot:
every action is one CLI command, and the app can show it.

## Purpose
The Server layer gives every Desktop real VM and container capability (QEMU +
KVM through `pengyos-vm`, rootless podman, distrobox, Quadlet), but all of it is
terminal-only. On the Server, `pengy-vmm` covers VMs in a browser. The Desktop
has nothing graphical: no way to see what is running, open a VM's screen, make a
dev box, or keep a container running at boot without knowing the CLI.

## Principles
1. **The CLI is the backend.** The app runs `pengyos-vm`, `distrobox`, `podman`
   and `systemctl --user` as the user, reads their JSON, and never edits their
   state files (`~/vms/NAME/vm.json`, podman's storage) directly. The one
   exception is writing Quadlet unit files, which are the interface.
   Every action has *Show command*, which prints the exact command line, so the
   app also teaches the CLI.
2. **Fix the CLI, not the app.** When the app needs something the CLI cannot do
   or say (a field, progress, a machine-readable error), the change goes into
   the CLI first, with its own tests, and is useful from a terminal too.
3. **Rootless and per-user.** Your VMs, your boxes, your containers. System
   (rootful) containers and system Quadlets are listed read-only in v1, marked
   *system*, with the command to manage them.
4. **Pure core, thin shell** (the pengyos-disks layout): `lib.rs` is std only and
   `cargo test`-able with no display, no podman and no QEMU (parsing the tools'
   JSON, the action → argv plans, validation, the Quadlet writer, the RFB
   protocol); `backend.rs` runs the commands; `app.rs` (feature `cosmic`) is the
   window; `vnc.rs` is the screen widget.

## Interface
- `pengyos-machines` — the window.
- `pengyos-machines --list [--json]` — what the window would show, no window
  (for SSH and tests).
- Desktop file `io.catbee.pengyos.Machines.desktop`, `Categories=System;`,
  keywords: vm, virtual machine, qemu, kvm, container, podman, docker, distrobox,
  toolbox, quadlet.

## The window
A nav bar on the left with three pages, and a status line that says what is
missing (no `/dev/kvm`, so VMs are slow; linger off, so things stop at logout).

### Virtual machines (`pengyos-vm`)
- **List**: name, state, CPUs, memory, network, SSH address, autostart; from
  `pengyos-vm list --json`, polled every 2 s while the window is visible.
- **New VM…** (a dialog):
  - the source: an installer ISO (file picker) or a disk image (a file or URL);
  - the name, CPUs, memory, disk size;
  - NAT or bridge (bridge only when `pengyos-vm bridge status` says it's on);
  - port forwards;
  - your SSH key;
  - UEFI or BIOS.
  Runs `pengyos-vm create`. Image downloads show progress (needs the CLI's
  `--progress`, below).
- **Per VM**: Start, Shut down, Force off, Screen, Console, Open SSH in a
  terminal, Snapshots, Start at boot, Eject ISO (offered when it has one), Settings
  (CPUs, memory, forwards: `pengyos-vm set`, when stopped where QEMU needs it),
  Delete (asks first, and names the disk it deletes).
- **Screen**: the built-in VNC view (below), in the VM's page or popped out to
  its own window. Opens by itself after *Start* for an ISO install.
- **Snapshots**: a list (time, label, size) with Take snapshot, Roll back
  (asks, switches the VM off, reverts, starts it again) and Delete; the CLI's
  `pengyos-vm snapshot` (below). Disk only, for "try something, roll back, try
  it another way"; not saved memory state.
- **ISO library**: *New VM* picks an ISO from `pengyos-vm iso list` or adds one
  (a file, or a URL downloaded with progress).
- **Console**: the serial log (`pengyos-vm log`), live, with a line to type into.

### Dev boxes (`distrobox`)
- **List**: the user's distrobox containers (podman containers with the
  `manager=distrobox` label), with image, state, size.
- **New box…**: a distro from a short list (Ubuntu LTS, Fedora, Debian, Arch,
  plus "any image"), a name, *with NVIDIA* on NVIDIA editions (`--nvidia`),
  an optional separate home. Runs `distrobox create`; the pull shows progress.
- **Per box**: Open in Terminal (`distrobox enter`, in COSMIC Terminal), Stop,
  Export an app to the launcher (`distrobox-export --app`, lists the box's
  `.desktop` files), Upgrade (`distrobox upgrade`), Delete.

### Containers (`podman` + Quadlet)
Two lists: **Services** (Quadlet units) and **Containers** (everything else that
is not a dev box), plus **Images**.
- **Services**: each `~/.config/containers/systemd/*.container` with its unit
  state, autostart (as `pengyos-services` reports it: `yes`, `at-login-only`…),
  ports and the firewall's view of them (`pengyos-services status --json`).
  Start, Stop, Restart, Logs (`journalctl --user -u`), Edit (the unit file in
  the text editor, then `systemctl --user daemon-reload`), Remove (stops it and
  deletes the file; the volumes stay unless you tick them).
- **Containers**: `podman ps -a --format json`. Start, Stop, Restart, Logs,
  Open a shell (`podman exec -it … sh` in COSMIC Terminal), Remove.
  **Keep running at boot…** turns one into a Quadlet service: it writes a
  `.container` file from the container's image, ports, volumes, environment and
  restart policy (`podman inspect`), shows the file before saving, starts the
  service and turns linger on (says so), then removes the original container.
- **Run a container…**: image (with a pull), name, ports, volumes (a folder
  picker), environment, *as a service* (writes a Quadlet instead of
  `podman run`). The GPU is a tickbox on NVIDIA editions (`--device
  nvidia.com/gpu=all`).
- **Images**: `podman images --format json`; Pull, Remove, Prune unused (asks).

System (rootful) containers and `/etc/containers/systemd` services appear in
their lists greyed, marked *system*, with the command to manage them; no polkit
in v1.

## The VNC view
A widget that speaks RFB 3.8 to QEMU's VNC server; written for this app, not a
port of a VNC client. QEMU is local, so v1 needs only:
- **Security type None**, over a **Unix socket** (below). No passwords, no TLS:
  the socket's file permissions are the access control.
- **Encodings**: Raw, zlib and ZRLE (QEMU's server offers these, not CopyRect;
  Tight is not needed on a local socket); DesktopSize (the guest changes resolution); the QEMU extended key event
  (scancodes, so any keyboard layout reaches the guest correctly) and the
  pointer through QEMU's USB tablet (absolute, no grab needed).
- Input: keyboard while the view has focus, with a menu for Ctrl+Alt+Del and
  the keys COSMIC keeps for itself; *Scale to window* or 1:1; clipboard is
  out of scope (QEMU's VNC has no clipboard without a guest agent).
- The protocol code (handshake, messages, decoders) is in `lib.rs`, tested
  against captured byte streams; the widget only draws frames and sends input.

## CLI changes this needs (land first, each tested)
1. **`pengyos-vm`: a VNC Unix socket.** Today the screen is VNC on
   `127.0.0.1:59xx` with no password, so **any account on the machine can watch
   and type into any user's VM**. Add `-vnc unix:~/vms/NAME/vnc.sock` (the
   folder is 0700), show `"vnc_socket"` in `show --json`, and keep TCP only when
   asked (`set NAME --vnc-tcp on`, for `ssh -L` from another machine). This is a
   fix on its own, for the Server too; `pengy-vmm` and `pengyos-vm vnc` follow.
2. **`pengyos-vm create --progress`**: one JSON line per step (download bytes,
   unpack, convert, resize) so the app shows a bar; the plain output stays.
3. **`pengyos-vm list --json` / `show --json`**: add `kvm` (accelerated or not),
   `linger`, the ISO's presence, and an error field for a VM whose `vm.json`
   does not read, instead of skipping it.
4. **`pengyos-vm snapshot NAME create [LABEL] | list [--json] | revert SNAP |
   delete SNAP`** (operator, 2026-11-27: disk state only). Internal qcow2
   snapshots of `disk.qcow2`. Running VM: QMP
   `blockdev-snapshot-internal-sync` on disk0. That's crash-consistent, like
   a power cut, which is fine for a "before I try X" snapshot; `create` says
   so, and a stopped VM gives a clean one. Stopped VM: `qemu-img snapshot -c`.
   `vars.fd` (raw) is copied to `snapshots/SNAP.vars.fd` and restored with
   the disk. `revert` needs the VM off: it asks (or `--yes`), stops it
   (`--force` after the power-button wait), runs `qemu-img snapshot -a`,
   restores `vars.fd`, and starts it again if it was running. `list` shows
   time, label and size (from `qemu-img info --output=json`). `delete`
   frees the space. `show --json` gains `snapshots`.
5. **`pengyos-vm iso list [--json] | add FILE|URL [--name N] | remove N`**: an ISO
   library in `~/vms/isos`. `add` copies a file, or downloads a URL (with
   `--progress` JSON lines). `create --iso NAME` takes a library name as well as
   a path. `remove` refuses an ISO a VM still has attached.
6. **Errors**: every `pengyos-vm` failure exits non-zero with one plain line on
   stderr (most already do); the app shows that line as is.
Dev boxes and containers need no CLI changes: podman's JSON is enough, and the
distrobox commands are run as they are.

## pengy-vmm (the Server's browser UI) gets the same
Operator, 2026-11-27: "basics plus the in-browser screen too". Over the same
`pengyos-vm` commands:
- **ISO library**: upload an ISO from the browser. It streams into
  `~/vms/isos`, checks free space first, and deletes a partial file on abort.
  Add one by URL (`pengyos-vm iso add`, with progress); remove one.
- **New VM** picks an ISO from the library, or a disk image from a list of the
  images in `~/vms/images` and the cache, instead of a typed path. A URL still
  works.
- **Snapshots**: list, take, roll back (asks first), delete.
- **Screen in the browser**: noVNC (MPL-2.0), vendored in the image and served
  by pengy-vmm itself (no CDN). pengy-vmm proxies a WebSocket to the VM's
  `vnc.sock`, so the screen needs no new port and sits behind the same token
  as every other action. QEMU's own `websocket=` option is not used: it
  listens on TCP. From another machine it is the same `ssh -L` to pengy-vmm
  as today.

## Packaging
- Rust crate `tools/pengyos-machines` (MIT), libcosmic pinned like
  pengyos-disks, built by `scripts/build-cosmic.sh` (add it to the `for app in`
  list), installed by `board/desktop/post-build.sh` (guarded like every app),
  and its desktop file in the Desktop overlay. Desktop and Desktop NVIDIA only.
- Runtime needs, all already in the Desktop: `pengyos-vm`, QEMU, podman,
  distrobox, systemd user manager, COSMIC Terminal (`cosmic-term -e`).
- Wiring check: `scripts/tests/test_cosmic_app_wiring.py` gains it.

## Out of scope for v1
Rootful containers and system Quadlets (read-only); pods, `.kube`, `.network`
and `.volume` Quadlet files (listed, not edited); snapshots with memory state
(disk only, by choice); USB/PCI passthrough
and GPU passthrough to VMs; SPICE, audio and a VM clipboard; remote hosts (the
Server has `pengy-vmm`); Compose files.

## Order
1. The `pengyos-vm` changes: the VNC socket first (it is also a security fix),
   then snapshots, then the ISO library. Then pengy-vmm: upload, picker,
   snapshots, and the browser screen (above).
2. Machines with the VM page and the VNC view.
3. Dev boxes.
4. Containers and Quadlet services.
Each step can ship on its own; the window shows only the finished pages.

## Verification
- `cargo test` on `lib.rs`: the tools' JSON (fixtures captured from a real
  Desktop), every action's argv, the Quadlet writer (output round-trips through
  `podman-system-generator --dryrun`), and the RFB code against captured
  sessions.
- A QEMU run on the build host: the app's backend against a real PengyOS Core
  VM (create, start, the VNC handshake and a frame, stop, delete).
- Real hardware: behobasement (Desktop), and beholaptop for Desktop NVIDIA
  (a GPU container and an NVIDIA dev box).
