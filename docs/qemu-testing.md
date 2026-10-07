# Testing PengyOS under QEMU — boot, SSH, and screenshots

This is the runbook for building a full image, booting it, and *observing* it.
The cheap checks (tool suites, `scripts/pengyos_gate.py`, `compose-defconfigs.py
--check`) catch wiring mistakes; the image build and boot are build-host work, and
this document covers them.

It exists because QEMU hides things and lies about others. A green boot is
necessary but not sufficient, and "look at the desktop" is harder than it sounds —
these are the techniques that actually worked, with the traps recorded so the next
agent doesn't rediscover them.

> Everything here was run on the build host (behoserver) against images built with
> `tools/pengyos-build`. Where a technique is fragile or VM-only, that is called out.

---

## 0. Prerequisites

A provisioned build host ([`../scripts/provision-build-host.sh`](../scripts/provision-build-host.sh))
with:

- `qemu-system-x86_64` with KVM (`/dev/kvm` writable);
- UEFI firmware — `/usr/share/OVMF/OVMF_CODE_4M.fd` + `OVMF_VARS_4M.fd`, or the
  single-file `/usr/share/ovmf/OVMF.fd`;
- optionally `swtpm` (measured boot), `Xvfb` + `ffmpeg` (Desktop screenshots);
- a built image, e.g.
  `~/build/pengyos/output/<target>/images/<target>-x86_64.img`.

Build one with:

```sh
cd tools/pengyos-build
python3 -m pengyos_build list  --targets ../../targets.toml
python3 -m pengyos_build build pengyos-core --targets ../../targets.toml
```

---

## 1. The headless boot harness — `tools/pengyos-qemu-verify`

The default, scriptable way to boot. It launches QEMU headless, waits for readiness
markers on the serial console, asserts a default check set, and emits machine-readable
results.

```sh
cd tools/pengyos-qemu-verify
python3 -m pengyos_qemu_verify boot \
    ~/build/pengyos/output/pengyos-core/images/pengyos-core-x86_64.img \
    --uefi --json /tmp/boot.json --junit /tmp/boot.xml
```

| Flag | Meaning |
|---|---|
| `--uefi` | UEFI/OVMF disk boot (PengyOS images are UEFI/GRUB; use this). |
| `--kernel BZIMAGE` | Boot a kernel directly via `-kernel` (bare-metal path) instead of firmware. |
| `--tpm` | Attach a `swtpm` TPM2 device (measured-boot testing). |
| `--ram MB` | Guest RAM (default 512; give the Desktop more). |
| `--timeout SECONDS` | Readiness deadline (default 120). |
| `--expect REGEX` | Readiness marker; repeatable (default `login:`). |
| `--hostfwd SPEC` | Forward a host port to the guest (see [§4](#4-ssh-into-a-booted-guest)). |
| `--no-snapshot` | Write changes through to the image (default is a throwaway overlay — the artifact is never modified). |
| `--json OUT` / `--junit OUT` | Write the result report (written even on failure). |
| `--dry-run` | Print the resolved QEMU argv and exit (no boot). |

- **Exit codes:** `0` ready + all checks pass; `1` ran but failed (timeout / panic /
  failed check / missing image); `2` usage error.
- **Default checks:** `reached-login` and `no-kernel-panic`, evaluated over the
  captured serial log.
- **`probe <img>`** prints the exact argv without launching — the fastest way to see
  what firmware/devices the harness picks.
- **`smoke`** runs a committed **smoke matrix** (a TOML of variants + regex checks):
  `smoke/core.toml` (the default) and `smoke/server.toml`, each grounded in real
  serial logs. A matrix finds its image under the build root, so after a build
  `pengyos-qemu-verify smoke` (or `--matrix smoke/server.toml`) needs no path.
  `pengyos-desktop-session` ships a VM-testable Desktop smoke.

> **Artifact safety:** the default is `snapshot=on`, so a verification boot opens the
> image read-only and leaves its hash unchanged. Only pass `--no-snapshot` when you
> *need* to persist guest state (e.g. an installer run).

---

## 2. Watching the boot — the serial console

The image prints everything to **ttyS0** (GRUB adds `console=ttyS0,115200` only
when it finds a real UART at COM1, which QEMU always has; real hardware without
a serial port boots without it). The harness captures it to a serial log in
its workdir; `tail` it to see where a slow or failed boot stopped.

For a live, interactive console, launch QEMU yourself with a serial backend:

- **capture to a file:** `-serial file:/path/serial.log`
- **attach a socket (drive it from another process):** `-serial unix:/path/ser.sock,server,nowait`
- **show it as a QEMU virtual console:** `-serial vc` (switch with **Ctrl-Alt-2**) —
  this is what [`../scripts/run-qemu-disk.sh`](../scripts/run-qemu-disk.sh) uses as a
  fallback.

**Driving the console non-interactively.** The serial getty **auto-logs-in root**, so
you can send commands over the socket with a tiny client — useful for diagnosing a
guest whose SSH is not up (e.g. `systemctl status sshd.service`,
`journalctl -b -u sshd.service`):

```python
import socket, time
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect("/path/ser.sock")
time.sleep(15)                                   # let it reach the login prompt
for cmd in ["systemctl --no-pager -l status sshd.service",
            "journalctl -b -u sshd.service --no-pager | tail -20"]:
    s.sendall(cmd.encode() + b"\n"); time.sleep(2)
    print(s.recv(65536).decode("utf-8", "replace"))
```

This is how the 2026-09-28 Server SSH failure was root-caused: TCP accepted but no
banner, and the journal showed `Bind to port 22 ... Address already in use`.

---

## 3. Booting interactively — `scripts/run-qemu-disk.sh`

For a window you can watch and type into (the human path), use the launcher. It boots
the newest `dist/pengyos*.img` (or a path you give it) under QEMU + OVMF:

```sh
scripts/run-qemu-disk.sh                       # newest image in dist/
scripts/run-qemu-disk.sh path/to/image.img
PENGYOS_RAM=2048 PENGYOS_SMP=4 scripts/run-qemu-disk.sh     # more resources
PENGYOS_DRYRUN=1 scripts/run-qemu-disk.sh                   # print the argv, don't boot
```

Env overrides: `PENGYOS_RAM` (1024), `PENGYOS_SMP` (2), `PENGYOS_DISPLAY` (`gtk`;
`sdl`/`none` ok), `PENGYOS_VGA` (`std`), `PENGYOS_RW=1` (write through instead of
`snapshot=on`). It copies OVMF `VARS` to a temp file (a writable variables store is
required) and cleans it up on exit.

---

## 4. SSH into a booted guest

The images run a **key-only** `sshd` (`PermitRootLogin prohibit-password`); the
authorized key is injected **at build time**, so build the target with a key set:

```sh
cd tools/pengyos-build
PENGYOS_AUTHORIZED_KEYS_FILE=~/.ssh/id_ed25519.pub \
  python3 -m pengyos_build build pengyos-server --targets ../../targets.toml
# the board's post-build.sh logs: "injected 1 SSH public key(s) ..."
```

Then boot with a forwarded port and connect. With the harness:

```sh
cd tools/pengyos-qemu-verify
python3 -m pengyos_qemu_verify boot <IMAGE> --uefi --hostfwd tcp::2222-:22 --timeout 90 &
ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_ed25519 \
    -p 2222 root@127.0.0.1
```

Or launch QEMU yourself and keep it running (so you can SSH while it is up). The exact
argv the harness resolves for a bare boot is:

```sh
qemu-system-x86_64 -M q35 -m 1024 -smp 2 -enable-kvm -cpu host \
  -bios /usr/share/ovmf/OVMF.fd \
  -drive file=<IMAGE>,if=virtio,format=raw,snapshot=on \
  -nic user,hostfwd=tcp::2222-:22 \
  -display none -serial file:/tmp/serial.log -monitor none -no-reboot
```

Run one VM per host port (`2222`, `2223`, …) if you boot several concurrently.
`ssh -o StrictHostKeyChecking=no …` is needed because the guest host key is generated
freshly at build time.

> **Gotcha (2026-09-28):** a guest whose `sshd` is *listening* can still be
> unreachable — "Connection timed out during banner exchange" means TCP connected but
> no SSH banner. Diagnose via the serial console ([§2](#2-watching-the-boot--the-serial-console)),
> not by retrying.

---

## 5. Validating the Desktop — screenshot it, don't just trust it

A graphical session can be "up" (systemd says the units are active) while the screen
is black. **You must look at pixels.** But every easy capture route fails for a GL
display:

- QEMU **`screendump`** returns *"no surface"* for GL displays (`egl-headless`,
  `gtk,gl=on`).
- Reading the guest's **`/dev/fb0`** shows black — fbdev emulation is bypassed once a
  compositor owns KMS.
- The guest's own **GNOME/portal screenshot APIs** are blocked (where a GNOME session
  is involved), and **GL XWayland windows are not in the X server's pixmap**, so
  `ffmpeg x11grab` / `import` of an X client capture black.

**What works:** run QEMU *inside Xvfb* with the **SDL display + GL** and capture the
Xvfb screen with `ffmpeg x11grab`. SDL uses GLX (llvmpipe) rather than EGL, which is
what makes it work on Xvfb.

`pengyos-qemu-verify desktop INSTALLER_IMAGE --out PREFIX` does all of this for an
installer stick: it types a probe on the guest's serial console and checks the
answer - root got a shell, cosmic-comp, cosmic-panel and the installer GUI are
running, and the system reports no failed unit. `scripts/release.sh` runs it for
every Desktop. The steps below are the manual version.

```sh
# 1. a virtual X server
Xvfb :99 -screen 0 1920x1200x24 &

# 2. boot the Desktop with a virtio-gpu-gl (virgl) device on SDL/GL, inside Xvfb.
#    `env -u WAYLAND_DISPLAY` is ESSENTIAL — otherwise SDL inherits the real
#    session's Wayland display and the window lands there, not on :99.
env -u WAYLAND_DISPLAY DISPLAY=:99 SDL_VIDEODRIVER=x11 SDL_VIDEO_WINDOW_POS=0,0 \
    LIBGL_ALWAYS_SOFTWARE=1 \
  qemu-system-x86_64 -machine q35 -accel kvm -cpu host -m 2048 -smp 4 \
    -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
    -drive if=pflash,format=raw,file=/tmp/pengyos-OVMF_VARS.fd \
    -drive file=<DESKTOP_IMAGE>,if=virtio,format=raw,snapshot=on \
    -vga none -device virtio-gpu-gl-pci -display sdl,gl=on \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0 -no-reboot &

# 3. once the session is up, grab the guest screen (find the window origin at :99)
ffmpeg -f x11grab -video_size 1280x800 -i :99+<winX>,<winY> -frames:v 1 desktop.png
```

**What a good screenshot shows:** the disk image stops at the COSMIC greeter; the
installer stick (`…-installer.img`) logs straight into a session — the panel, the
dock, the PengyOS wallpaper and the installer's welcome window. The SDL window sits
at Xvfb's centre (`xwininfo -root -tree | grep QEMU` gives its geometry); grabbing
the whole `:99` screen is simplest.
Cross-check the session with `ps` over the serial console (root is logged in on
`ttyS0`): `cosmic-comp`, `cosmic-session`, `cosmic-panel`, its applets,
`cosmic-launcher` + `pop-launcher`, `cosmic-bg`, `cosmic-osd` should all be running,
and `systemctl --failed` empty. Give the serial port a unix socket
(`-serial unix:$HOME/tmp/q.sock,server=on,wait=off`) — keep the path under 108
bytes, or QEMU refuses it.

**Requirements / traps:**

- The guest needs Mesa's **virgl** Gallium driver
  (`BR2_PACKAGE_MESA3D_GALLIUM_DRIVER_VIRGL=y` — already in the Desktop defconfig) and
  a GLES/EGL path.
- The host needs Xvfb + ffmpeg + a software GL (llvmpipe). `gtk,gl=on` will **not**
  work on Xvfb (DRI3 unavailable → `EGL_BAD_ACCESS`); use SDL.
- Plain virtio-gpu (no `-gl-pci`) can wedge some compositors (weston needed
  `--renderer=pixman`); for COSMIC use `virtio-gpu-gl-pci` + SDL/GL as above.
- A `-vga std` device + `-serial vc` (the interactive launcher) is fine for watching a
  *console*, but do **not** expect it to render a GL desktop.

---

## 5b. Testing the installer — stick + blank target disk

The Desktop build emits two images: `pengyos-desktop-x86_64.img` (what gets
installed) and `pengyos-desktop-x86_64-installer.img` (the USB stick = that
image + a `PENGYOS-PAYLOAD` partition carrying it; the partition is what starts the
installer). Test the stick as a USB device with a blank disk next to it, in the same
Xvfb/SDL setup as §5:

```sh
truncate -s 16G target.img
qemu-system-x86_64 … -device qemu-xhci,id=xhci -device usb-tablet -device usb-kbd \
  -drive if=none,id=stick,file=<…>-installer.img,format=raw,snapshot=on \
  -device usb-storage,bus=xhci.0,drive=stick,removable=on,bootindex=1 \
  -drive if=none,id=tgt,file=target.img,format=raw \
  -device virtio-blk-pci,drive=tgt,bootindex=2 -no-reboot …
```

- The GUI (`pengyos-install-gui`) opens by itself in the live session; the console
  installer is `pengyos-install-tui install` (serial/SSH); both run
  `pengyos-install image`. The stick is listed but not selectable.
- **Restart now** exits QEMU (`-no-reboot`); boot again with only `target.img`
  (`bootindex=1`). Expect the layout of `docs/state-layout.md`: `/` read-only
  from root-a (8 GiB), `/data` = partition 4, `/etc` an overlay, `/var` `/home`
  `/root` bound from `/data`; the chosen hostname and user; and
  `pengyos-live-installer.service` skipped (`ConditionResult=no`).
- To check state survives an update: boot the stick again and rewrite root-a by
  hand (`zstd -dc /run/pengyos/payload/rootfs.ext4.zst | dd of=/dev/vda2`, then
  `e2fsck -fy`, `resize2fs`, `tune2fs -U random -L pengyos-root-a`), boot the
  target — everything in `/home`, `/etc` (hostname, accounts, SSH host keys, NM
  profiles) must still be there.
- `usb-tablet` + QMP `input-send-event` (absolute coordinates) drive the mouse;
  QMP `send-key` types. Keys are queued with their hold time — pause after typing
  before clicking the next field, or the text lands in the wrong one.
- No sudo on the build host? `apt download xvfb qemu-system-gui` + `dpkg -x` into a
  scratch dir works: run Xvfb from there, and point `QEMU_MODULE_DIR` at a directory
  holding symlinks to both the system QEMU modules and the extracted `ui-sdl.so`.
  Unix socket paths must stay under 108 bytes (use relative paths).

---

## 6. VM ≠ real hardware — the caveats

The VM is necessary, never sufficient. Known divergences to keep in mind:

- **Expected VM-only failures.** On the Desktop image, `wireplumber.service` and
  `bluetooth.service` fail under QEMU because there is no sound/BT device — this is
  **not a regression**. Confirm audio/BT on real hardware, not in the VM.
- **The VM can hide a broken real stack.** The Desktop rendered fine in QEMU while the
  **real N100 showed a grey screen** (i915 wedged — no firmware — and Mesa built
  without `iris`, forcing software GL). QEMU's virtio/virgl path masked all of it, so
  a clean VM boot of the Desktop is *not* evidence the real GPU works.
- **GL in QEMU is software** (virgl/llvmpipe): do not read performance from it.
- **USB enumeration:** the N100 will not enumerate a **GPT-only** stick — the image and
  the flash path must use **hybrid MBR+GPT** (`../scripts/flash-usb.sh` does the
  post-`dd` `sgdisk -e -h 1` repair).
- Always follow a VM pass with a real-hardware boot on the target machine.

---

## 7. Quick reference

| Task | Command |
|---|---|
| Build a target | `cd tools/pengyos-build && python3 -m pengyos_build build <target> --targets ../../targets.toml` |
| Headless boot + checks | `cd tools/pengyos-qemu-verify && python3 -m pengyos_qemu_verify boot <IMG> --uefi` |
| Show the QEMU argv | `pengyos-qemu-verify probe <IMG> --uefi` |
| Run a smoke matrix | `pengyos-qemu-verify smoke` (Core) or `smoke --matrix smoke/server.toml` |
| Interactive boot (window) | `scripts/run-qemu-disk.sh <IMG>` |
| SSH in | build with `PENGYOS_AUTHORIZED_KEYS_FILE=… .pub`, boot `--hostfwd tcp::2222-:22`, `ssh -p 2222 root@127.0.0.1` |
| Watch serial | `-serial file:LOG` (tail) or `-serial vc` (Ctrl-Alt-2) or a unix-socket client |
| Screenshot a GL desktop | Xvfb + `-display sdl,gl=on` + `-device virtio-gpu-gl-pci`, then `ffmpeg -f x11grab` |
| Flash to USB | `sudo scripts/flash-usb.sh <IMG> /dev/sdX` |
