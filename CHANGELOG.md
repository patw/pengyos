# Changelog

All notable changes to **PengyOS** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Releases (the OS images) use **calendar versions, `YY.MM.N`**: `26.09` is the
release line (year and month), `26.09.3` the fourth image published that
month. The number lives in [`VERSION`](VERSION); see CONTRIBUTING.md
("Versioning"). Individual tools under
[`tools/`](tools/) each keep their own `CHANGELOG.md`.

> This file records the **system-level** milestones — a build that boots, an
> edition that becomes real, an install/update decision that lands, and the
> integration findings that got us there. Per-tool change history lives with the
> tool. For the *current* state of each edition, see the edition table and the
> "What isn't ready" list in [`README.md`](README.md).

## [Unreleased]

### Fixed: the battery reading froze after suspend — UPower 1.91.5 as a fork (found on beholaptop, 26.10.35)

- After resume, the panel and Settings → Power kept the battery reading from the
  moment of wake-up: 75% and "7.1 hours left" for 83 minutes while the kernel read
  48% and 1.7 h. UPower 0.99.19 (Buildroot 2026.08's pin, a recipe Buildroot has not
  touched since 2022) pauses polling for sleep; a poll that fires while paused never
  re-arms, and resume only clears the flag. About one suspend in five, measured. A
  restart of `upower.service` or an AC plug/unplug brought it back. UPower 1.91.5
  re-arms the poll on resume.
- **`upower-pengyos` 1.91.5** replaces stock upower in the Desktop editions. It also
  carries the capacity_level fix, so the `patches/upower/0.99.19` set is deleted.
  **`thermald-pengyos`** is stock thermald 2.5.6 rebuilt against it: stock thermald
  selects stock upower, which would otherwise be built beside the fork.
  power-profiles-daemon now depends on the fork. Ledger entry `upower-1.91.5-fork`
  (fork 4 of 10); pengyos-nightly watches upstream tags (`upower-fork`).

### Fixed — Server metrics ran once per boot, not hourly; backups once per boot, not nightly (found on beholaptop, 26.10.35)

`pengyos-monitor.service` and `pengyos-backup.service` are `Type=oneshot` units
started by timers, and both set `RemainAfterExit=yes`. After the first run the
unit stays "active (exited)" until shutdown, and a timer does not start a unit
that is already active: `systemctl list-timers` showed `pengyos-monitor.timer`
with no next run, and `/var/log/pengyos-monitor.log` had one line per boot
(two in 21 hours). The backup timer (off by default) had the same flaw, so a
machine that turned it on got one backup per boot and nothing after. Both
units drop the option; the `pengyos-server-backup` tool's generated unit did
the same and is fixed too. Every Server-based edition (Server, Desktop and
their NVIDIA variants) has this since the overlay was first written.

### Fixed — no icons in GTK3 dialogs; boxes in the terminal (found on beholaptop, 26.10.32)

**Icons.** gdk-pixbuf had no SVG loader: Buildroot builds librsvg without it
(`--disable-pixbuf-loader`), and the image did not build librsvg at all. The Cosmic
icon theme is SVG only (704 icons, no PNGs), so a GTK3 window running on the host
could load none of them ("Couldn't recognize the image file format"). These are
xdg-desktop-portal-gtk's dialogs: the app chooser a Flatpak app gets when it opens a
file, and Print. The Desktops now build librsvg with its pixbuf loader (an override
in `external.mk`), and the target's `loaders.cache` is regenerated after it so it
lists SVG. Flatpak and COSMIC apps render SVG themselves and were not affected.

**Terminal boxes.** The starship prompt's default icons (U+E0A0 git branch, U+E718
Node.js) are Nerd Fonts glyphs in the Private Use Area, and no shipped font had them.
Claude Code's mode line uses U+23F5, a standard Unicode symbol that DejaVu, Fira and
Noto Color Emoji also lack. Two new font recipes: `font-symbols-nerd` (the icon-only
Symbols Nerd Font, MIT) and `font-noto-symbols2` (Noto Sans Symbols 2, OFL, as in
Ubuntu's fonts-noto-core). fontconfig's per-glyph fallback finds them under any
terminal font, so the default `monospace` font is unchanged (upstream's
`10-nerd-font-symbols.conf`, which makes the icon font the preferred `monospace`, is
not installed). Over SSH the client draws the glyphs, so Server is unchanged.

### Fixed (26.10.34) — Firefox and Chrome had a close button only, again (found on beholaptop, 26.10.32)

The portal fix from 26.10.18 still works: xdg-desktop-portal-gtk serves
`org.gnome.desktop.wm.preferences button-layout`. But on a fresh install it served
the schema default, `appmenu:close`. `cosmic-settings-daemon` writes its four GNOME
keys at login as four concurrent `gsettings set` processes. The image has no dconf,
so GSettings uses its keyfile backend, where each writer rewrites the whole file,
and the writes race: beholaptop's fresh Desktop NVIDIA install kept the two font
keys and lost `button-layout` and `icon-theme` (reproduced on the build host: 6 of
50 runs lose `button-layout`). Machines whose keyfile once got the key, like
behoweather, were not affected.

The Desktop's gschema override (`board/desktop/gsettings/90-pengyos.gschema.override`)
now sets COSMIC's own values as the defaults: `button-layout=':minimize,maximize,close'`
and `icon-theme='Cosmic'`. A lost write no longer changes anything. COSMIC Settings
still writes the key when the user turns a button off. `scripts/tests/test_gsettings_defaults.py`
compiles the override against the real schemas with `--strict` and reads the keys back
(the 26.10.18 fix had no test). Existing installs pick it up on update; to fix a
running session now: `gsettings set org.gnome.desktop.wm.preferences button-layout ':minimize,maximize,close'`.

The override fixes only the defaults; the daemon also writes `color-scheme` and
`gtk-theme` concurrently on every dark/light switch. So the Desktop editions now ship
**dconf** (new recipe `package/dconf`, 51.0, LGPL-2.1+), the GSettings backend
GNOME, Pop!_OS and Ubuntu use: `dconf-service` applies writes in order, and none are
lost. `scripts/tests/test_gsettings_dconf.py` runs the daemon's six concurrent writes
against dconf (the same test fails on the keyfile backend). With dconf installed,
GLib stops reading the old keyfile. A new user unit,
`pengyos-gsettings-keyfile-migrate.service`, loads it into dconf on the first login
after the update and renames it `keyfile.migrated-to-dconf`, so settings made by hand
(such as location services turned off) carry over.

### Added — eight pack-in games on the Desktop
- **Frostbite Solitaire** (Klondike), **Icefloe 2048**, **Glacier Blocks** (falling
  blocks), **Flappy Pengy**, **Penguin Pong**, **Thaw Invaders**, **Ice Lander** and
  **Ice Rogue**, in the App Library under Games on Desktop and Desktop NVIDIA. Each
  is a libcosmic app (`tools/pengy-*`); the image carries all eight in one binary,
  `tools/pengy-games` (host-built by `scripts/build-cosmic.sh`, installed by the
  Desktop post-build with a `/usr/bin/pengy-<game>` symlink per game), because eight
  separate binaries would carry eight copies of libcosmic: ~250 MB of the 4000 MB
  Desktop root, against ~37 MB for the shared one (fat LTO folds the duplicated iced
  runtimes). Launchers and icons are in the Desktop overlay, named by app id. The rules are `std`-only libraries
  with a headless replay harness, so `cargo test` and the gate need no display.
- They started as terminal games with Buildroot recipes; on the hardware they drew
  badly in COSMIC Terminal (`stty raw` turns off output processing, so every frame
  staircased), and a windowed game is what a Desktop wants. The terminal front-ends
  and the recipes are gone; Core and Server ship no games.
- Found while integrating them, fixed in the rules: Invaders' laser was hitscan
  (it killed through your own shields), Rogue dug stray corridors (a swapped
  argument), 2048's blocked slides wiped the undo, Klondike could not move part of
  a run, a crashed Lander run could fly on, and every game quit itself two seconds
  after game over and counted one game as two in its record.

### Changed (26.10.33) — no split-lock stall on the Desktops
- On Intel CPUs with split-lock detection the kernel stalls a thread ~10 ms on every
  split-lock trap. Steam's client traps (behoweather's N100 logged `steam`,
  `IPC:CSteamEngin` and `gldriverquery`), and some games under Proton trap often
  enough to stutter. Desktop and Desktop NVIDIA now set `kernel.split_lock_mitigate = 0`
  (`/usr/lib/sysctl.d/60-pengyos-split-lock.conf`): the log warning stays, the stall
  goes. Bazzite, CachyOS and Nobara ship the same value. The Servers keep the kernel
  default, where the stall stops one process from slowing every core. Nobara also
  patches its kernel to rate-limit bus locks to 100/s on CPUs with hardware bus-lock
  detection, after Steam's 32-bit client hung machines with ~1,000/s; not taken here
  (untested on our hardware, and Nobara calls it temporary).

### Fixed (26.10.33) — `su` logged two PAM errors every time
- Buildroot's util-linux `su.pam` lists `pam_lastlog.so`, but our Linux-PAM is built
  without it, so every `su` logged "PAM unable to dlopen(/usr/lib/security/pam_lastlog.so)"
  and "PAM adding faulty module" (seen on behoweather, 26.10.31). The line is `optional`,
  so `su` worked. The core post-build now removes it from `/etc/pam.d/su` and `su-l` in
  every edition; `su` does not need lastlog, and Debian's and Fedora's have none.

### Fixed (26.10.33) — laptop panel backlight: the driver the firmware asks for was not built
- On a hybrid laptop whose firmware reports brightness as EC-controlled over the NVIDIA WMI
  interface, the kernel resolves the backlight type to `nvidia_wmi_ec` — a branch decided before
  both the ACPI-video and the native ones. With `CONFIG_NVIDIA_WMI_EC_BACKLIGHT` unset, nothing
  registered a backlight: `/sys/class/backlight` stayed empty, brightness keys did nothing, and
  `cosmic-settings-daemon` fell back to probing DDC/CI on every `/dev/i2c-*` bus — **3.0 s of
  kernel time per brightness keypress**, inside its single D-Bus loop, so the volume keys stalled
  behind it too. Seen on beholaptop (Lenovo Legion 5 15ARH7H, Radeon 680M + RTX 3070 Ti,
  26.10.32): one press measured 2.99-3.08 s; with the driver built in, the same call returns in
  0.00 s and the keys adjust the panel. Desktop and Desktop NVIDIA now build it (`=y`: no
  firmware, and the device must exist before `cosmic-osd` reads `MaxDisplayBrightness` at
  startup). No kernel command line involved: `acpi_backlight=native` would override what the
  firmware explicitly reports.

### Changed (26.10.32) — the kernel gets what a distro kernel has, and the build checks it
- Our kernel is `x86_64_defconfig` plus three fragments, so anything nobody added
  was off, silently. A diff against Ubuntu 26.04's config found 7,514 options it
  enables that we do not. Nearly all are drivers for specific hardware. Checking
  ~150 infrastructure options turned up these, now on:
  - **Every edition** (core fragment):
    - cgroup v2 memory control and BPF (`MEMCG`, `BPF_SYSCALL`, `CGROUP_BPF`): on
      Core, systemd's `MemoryMax=`, `DeviceAllow=` and `IPAddressDeny=` were no-ops.
    - The BPF JIT, `PSI`, and `intel_idle` (Intel CPUs got only `acpi_idle`'s
      C-states).
    - Native PCIe hotplug (moved down from the Desktop fragment: NVMe bays and
      USB4 tunnels need it too), plus PCIe AER and DPC, so link errors are logged
      and recovered instead of hanging.
    - pstore in EFI variables, so a panic's log survives the reboot
      (`systemd-pstore` was already enabled).
    - Transparent huge pages (madvise) and the multi-generational LRU.
    - zswap stays off: swap is zram.
  - **Server**: SR-IOV and the SMB/CIFS client.
  - **Desktop** (laptops):
    - USB-C through UCSI, with DisplayPort alt mode.
    - Power modes: Lenovo GameZone and AMD PMF. Until now there was no
      `platform_profile`, so power-profiles-daemon had nothing to switch.
    - Intel DPTF.
    - `serial-multi-instantiate`: without it, Cirrus CS35L41-type speaker amps
      never bind, and many HP/Lenovo/ASUS laptops have silent speakers.
    - HDA codec power save on by default: about 6 W on battery on beholaptop.
- Kconfig drops a fragment line whose dependencies are unmet without a word (the
  existing `scripts/check-kernel-fragments.py` found eight in September, but
  only when someone ran it). The core post-build now runs it with `--built`
  against the kernel's resolved `.config` and **fails the build** on any dropped
  line. It also checks the new `board/core/kernel-required.txt`: options every
  edition relies on that only the base defconfig provides (the EFI stub, vfat's
  codepage for the ESP, built-in usb-storage for the live stick, systemd's hard
  requirements, ...), so a kernel bump can no longer take one away unnoticed.

### Fixed (26.10.32) — a serial login that restarted every 10 s; `switcherooctl` would not start
- Every edition enabled a login on `ttyS0`, whether or not the machine had a
  serial port. The kernel always creates `ttyS0`, so on a laptop with no UART
  (beholaptop, a Lenovo with a Radeon 680M and an RTX 3070 Ti, 26.10.31) agetty failed and
  restarted every 10 s, two journal lines each time, forever. The getty port is now
  `tty1`. `systemd-getty-generator` starts the serial login from
  `console=ttyS0`, which GRUB adds only when it finds a real UART (QEMU has one), so
  QEMU and machines with a COM port keep their serial login and autologin.
- `switcherooctl` started with the build host's Python
  (`#!…/output/<target>/host/bin/python3`), so it failed with "required file not
  found". The daemon, which is what COSMIC's "Launch using discrete graphics" uses,
  was unaffected. The shebang is now `/usr/bin/python3`.
- Checked on the same laptop: hybrid graphics work. CUDA, Vulkan and OpenGL work on
  both GPUs, natively and inside the Flathub 26.08 runtime (Steam, Heroic) with
  `GL.nvidia-615-78-08` and its 32-bit twin. The switcheroo variables pick the GPU
  inside the sandbox too.
- The dGPU never runtime-suspended. It stayed in D0 from boot, with
  `runtime_suspended_time` at 0 and ~23 W on battery, even with the session stopped
  and nothing holding `/dev/nvidia*`, so it was not COSMIC. The cause was
  `CONFIG_VGA_SWITCHEROO` being unset: then `snd_hda_intel` never turns on runtime PM
  for the GPU's own HDMI audio function, and the kernel's GPU/HDA device link keeps a
  GPU awake while its audio function is. Fixing that got the GPU to D3hot. The slot
  still never lost power: its root port is a hotplug port, and without
  `CONFIG_HOTPLUG_PCI_PCIE` the kernel refuses D3 for such ports. The Desktop kernel
  now has `CONFIG_VGA_SWITCHEROO=y`, and every edition has `CONFIG_HOTPLUG_PCI_PCIE=y`. Tested on the
  laptop: the GPU and its root port reach D3cold within seconds of boot, with
  `cosmic-comp` still holding `/dev/nvidia0` and video memory off. A CUDA+Vulkan
  job wakes it in 1.8 s, and it is back in D3cold within 25 s.
- Removed `60-pengyos-amdgpu-runtime-pm.rules`. Its `card[0-9]*` match also fired on
  every DRM connector and on the simpledrm card, and even on the real card its
  `autosuspend_delay_ms` write failed. An APU has no runtime PM ("Runtime PM not
  available"), and on dGPUs amdgpu already enables runtime PM itself
  (`pm_runtime_allow`, 5 s autosuspend). It was ~12 udev errors per boot and did
  nothing.

### Added (26.10.32) — crash dumps and `coredumpctl` (capped), build-ids on every binary
- Every edition now ships `systemd-coredump`. Before, `kernel.core_pattern` was
  `|/bin/false`: a crash left only the kernel's one-line "segfault at", and nothing
  at all for an abort. Now each crash logs a stack trace to the journal (libdw is
  in the image), and `coredumpctl list` / `coredumpctl info` show what crashed and
  where. Flatpak apps are covered: the kernel runs the handler outside the sandbox.
- Storage is capped in `/usr/lib/systemd/coredump.conf.d/50-pengyos.conf`, since a
  browser or Steam dump alone can be gigabytes: the store never exceeds 1G, never
  leaves less than 10G free, and a process over 2G keeps only its journal backtrace.
- Dumps are root-only files; each user can read their own. Verified in QEMU (Desktop):
  a `SIGSEGV`'d process was stored and `coredumpctl info` showed its stack. The
  image is stripped, so frames in our own code show as offsets (exported symbols
  such as libc's resolve); the build-id below is what matches a dump to the
  unstripped binaries in the build tree.
- Every binary the Buildroot toolchain links now carries a GNU build-id
  (`external.mk`): 36 bytes per ELF, no runtime cost. `BR2_REPRODUCIBLE` links with
  `--build-id=none`, because two output directories can hash differently; we build
  every release from the same paths, so that reason does not apply. glibc,
  coreutils, flatpak and the rest had none; COSMIC (built with the host toolchain)
  and the Go binaries already did. Everything relinks, so every edition is rebuilt
  from scratch.

### Changed (26.10.32) — the agent the images ship: Pengy 1.11.5 → 1.12.3
- `package/pengy` pinned 1.11.5 (images 26.10.24 onward) and stopped moving, while
  upstream released 1.12.0, 1.12.1, 1.12.2 and 1.12.3 — the newest on 2026-10-16,
  the day before this build. The pin now tracks 1.12.3, and the `pengy.hash` line
  is the sha256 of the cargo-vendored tarball Buildroot generates
  (`pengy-1.12.3-cargo6.tar.gz`), verified by a second `pengy-source` run;
  `LICENSE` is byte-identical. One package rebuilds; no toolchain change, so the
  existing build trees stay.
- What 1.12.x fixes in the shipped agent, in the order a user meets it: tool-call
  arguments are validated against the tool's own schema before anything runs, so a
  misplaced `dry_run` key or a stringified boolean is coerced or rejected instead
  of silently ignored (a "preview" that wrote files); `write_file`,
  `replace_in_file` and `apply_changes` write through a staged temp file renamed
  over the target, so a failed write leaves the original intact; provider timeouts
  (408/504, and a request that outlives `llm_timeout`) and transient 500/502/503
  are retried on the same ladder as 429/529 instead of ending the turn; a 200 with
  no choices, or a message with no content and no tool calls, is a failed turn
  rather than a blank answer saved as the assistant's reply; `run_bash`,
  `run_python` and `run_powershell` return the output they printed before a
  timeout killed them; two edits far apart in a large file diff as two small hunks
  instead of a 4000-line re-write; an endpoint/model that rejected an image is
  remembered, so later turns stop resending it; and the web prompt lease no longer
  cancels a long unattended run at about 30 minutes. The Qt GUI no longer
  segfaults when a file chip is removed or submitted (1.12.3).

### Fixed (26.10.31) — the nightly backup filled the drive; it is now off by default
- `pengyos-backup.timer` was enabled in Server and therefore in every edition built
  on it (both Desktops included). Its defaults tar all of `/etc` and `/home` every
  night to `/var/backups/pengyos` on the same disk and keep 7 copies, so a Desktop
  with a Steam library or large Flatpak data in `/home` ran out of space. The image
  disables it with a preset (`10-pengyos-server.preset`): dropping the
  `timers.target.wants/` link alone was not enough, because the image build's
  `systemctl preset-all` re-enabled it from the unit's `[Install]` section (the
  Server smoke matrix caught that). The Server post-build also removes the old link
  from existing build trees. Installed machines lose it with the next
  update (`/etc` is an overlay on the image's copy), unless someone enabled it on
  that machine. The units and `backup.conf` still ship: point `BACKUP_DEST` at
  another disk, then `systemctl enable --now pengyos-backup.timer`. Tarballs that
  already exist under `/var/backups/pengyos` stay on `data` until deleted by hand.

### Changed (26.10.31) — kernel 7.2.11, NVIDIA 615.78.08
- **Linux 7.2.5 → 7.2.11**, a point release in the same series, in every edition.
- **`nvidia-open` 610.57.04 → 615.78.08**, NVIDIA's current feature branch, in both
  NVIDIA editions. Against 610 the install list changed only in its side-versions
  (`libnvidia-egl-wayland2` 1.0.1 → 1.0.2, `libnvidia-egl-xcb` / `-xlib` 1.0.5 →
  1.0.6). The kernel module now also requests `ucodes_ga10x.bin` / `ucodes_tu10x.bin`
  beside the GSP firmware (`kernel-open/common/inc/nv-firmware.h`), so they ship too.
  `LICENSE` is byte-identical. Both bumps were made by hand: they are report-only
  to the nightly loop (`guards.DENIED`).

### Fixed (26.10.30) — no "Todos - Iced" in the App Library
- The Desktop post-build installed every `.desktop` file under the COSMIC source
  tree, and libcosmic vendors iced, whose `examples/todos` ships
  `iced-todos.desktop`. The App Library showed a "Todos - Iced" entry with no binary
  behind it. `examples/` is now skipped, and the stale file is removed from build
  trees that already have it. It was the only installed entry whose `Exec` was missing.

### Docs — SteamVR to a Quest 3S, assessed (research, nothing shipped)
- **The question:** can a PengyOS machine stream SteamVR games to the household Quest
  3S wirelessly? Answer: **the software stack is viable, our OS setup is not, and the
  house Wi-Fi is the hardest of the three problems.** Recorded in
  [`docs/steamvr-quest-wireless-viability.md`](docs/steamvr-quest-wireless-viability.md).
- **Why "not today" is a fleet fact, not a code fact:** the only machines running
  PengyOS (behoweather, behocraptop — N100 / Alder Lake-N iGPU) cannot do VR, and every
  VR-capable machine (behotv RTX 3070, behopc 3060 Ti, behoserver 2× 5060 Ti) runs
  Ubuntu. So this is a requirements analysis of Desktop NVIDIA, explicitly **not a
  measurement**.
- **The load-bearing contradiction:** US-DSK-4 makes Steam a Flatpak from the store,
  and Valve's support entry says SteamVR for Linux "cannot run properly within the
  unsupported Steam Snap or Steam Flatpak packages as they break both DRM leasing and
  asynchronous reprojection". On an image-based OS with no native package lane, that
  makes the store lane the wrong lane for VR.
- **Three gaps found by reading the tree and one live box** (behoweather, 26.10,
  kernel 7.2.5): the image ships **no Flatpak NVIDIA GL/Vulkan extension** — so on an
  NVIDIA machine Flatpak apps see Mesa/lavapipe, while `nvidia.config` states the
  extension as an expectation the image never arranges; **`CONFIG_NTSYNC=m` is built
  and never loaded** (no `modules-load.d` entry, `/dev/ntsync` absent), so Proton 11+
  VR games lose the primitive the Linux VR guidance treats as required; and **no NVIDIA
  application profile** ships, which is WiVRn's documented fix for NVENC parking the
  GPU in P2.
- **What is already right, and worth saying out loud:** kernel 7.2.5 ≥ the 6.14 ntsync
  floor, `uinput` auto-loaded, Valve's `60-steam-vr.rules`, Avahi active, and a
  firewall that already opens mDNS plus every inbound high UDP port — WiVRn's 9757 and
  Steam Link's 10400 need no configuration here.
- **Not proposed here:** a WiVRn/xrizer recipe. The Flathub Flatpak (v26.9, bundles
  xrizer + OpenComposite) answers the question; the recipe is the last step, not the
  first. The first step is the radio: the 2026-09 diagnosis (BotTalk
  `c7cb9f01bf05651ca3000000`) already proved the Deco mesh, not the OS, was the cause.

### Added — a Jev-class (System One) decision service, measured (prototype, not shipped)
- **The question:** can a small "JEV" model run in PengyOS as a service —
  Vulkan, statically compiled, tiny enough for a 16 GB laptop? **TypeSafe's Jev
  itself is closed-weight** (API only, no checkpoint). What is open are the
  Jev-class reproductions, and llama.cpp grew a native TypeSafe-compatible
  `/v1/systemone` endpoint on 2026-10-02 (`b11318`) serving `laya`, `openjev`,
  `clef`, `nimble`, `lev`, `kev`, `pplx-decider`, `vllm-decision2`, `vllm-d3`,
  `lfm2-d1`, `lfm2-d1-omni`.
- **Measured, not promised.** A static (`BUILD_SHARED_LIBS=OFF`), Vulkan-only
  `llama-server` (61 MB, links only libvulkan/OpenSSL/libstdc++/libc/z) serving
  **Laya (421 M, Apache-2.0)**: 8.4 ms per decision on an RTX 3070; **304 ms per
  decision and 146 MB peak RSS on a PengyOS Desktop Intel N100 using its
  integrated GPU as a Vulkan device** (405 ms / 96 MB CPU-only). d1-3B-Q4_K_M:
  16 ms, 2.08 GB peak. The same binary ran on PengyOS Desktop 26.10 unchanged.
- **Three packaging traps found by hitting them:** Buildroot 2026.08 has **no
  shaderc/glslc recipe**, so `BR2_PACKAGE_LLAMA_CPP_VULKAN=y` cannot configure
  (ggml-vulkan requires `glslc` + `SPIRV-Headers`); PengyOS ships **no
  `libgomp.so.1`**, so the default OpenMP build dies at exec —
  `-DGGML_OPENMP=OFF` is required; and the 2026.08 `llama-cpp` pin (`b8117`,
  2026-02-20) predates the decision API by eight months.
- **Licence position:** Laya Apache-2.0 (clean), d1-3B LFM Open License
  (Apache-2.0 + a $10 M revenue condition — needs an audit exception), OpenJev
  **CC-BY-NC-4.0 (must not ship)**.
- **Lands as** [`docs/jev-model-service-viability.md`](docs/jev-model-service-viability.md)
  + [`tools/pengyos-jev/`](tools/pengyos-jev/) (client, unit skeleton, recipe
  skeleton, 14 offline tests). **Nothing is enabled in an image.**

### Changed (26.10.29) — PDFs open in Evince once it is installed (Firefox until then)
- **`application/pdf=org.gnome.Evince.desktop;org.mozilla.firefox.desktop;`**
  in the Desktop's `mimeapps.list`. Evince is the human-signed `PDF` row in
  [`tools/pengyos-desktop-curation/curated.toml`](tools/pengyos-desktop-curation/),
  but the image does **not** install it: it is a Flatpak on the GNOME runtime,
  which nothing else in the image uses — measured on Flathub, ~290 MB of
  download and ~650 MB on disk on top of what Firefox already pulls, for a
  13 MB app, plus the runtime's own updates. Firefox's built-in viewer opens
  PDFs out of the box; Evince is one click in the Store, and the moment it is
  installed it becomes the PDF handler by itself. (`xdg-mime` skips a handler
  whose `.desktop` does not resolve, so the chain never answers "no method
  available for opening".) A first-boot unit that installed Evince for
  everyone was tried and dropped for that cost before release.
- **Why not a native (Lane 1) viewer, measured against the pinned tree:**
  Buildroot 2026.08's `mupdf` only builds X11/GLUT front ends (the Desktop is
  Wayland with no X11 stack), its `poppler` has no glib wrapper (Qt5 or utils
  only), and `zathura` is not a Buildroot package. No other Flathub viewer is
  cheaper: Papers, Zathura and Foliate need a GNOME runtime too, Okular the
  KDE one, Sioyek freedesktop 24.08 (Firefox is on 25.08). A libcosmic viewer
  over the MuPDF core is the real Lane 1 answer, left as a proposal.

### Added (26.10.29) — Firmware updates get a Desktop app (and fwupd finally runs)
- New tool [`tools/pengyos-firmware`](tools/pengyos-firmware/): the **Firmware**
  app (libcosmic) shows the devices fwupd can update, what each update goes to,
  its size and urgency, and what it needs from the person (wall power, applies
  while switched off, a restart, a replug), installs what is pressed with
  progress, offers **Restart now** for a staged update, and snoozes reminders —
  the Ubuntu "Firmware Updater" job, in the PengyOS shape. The session notifier
  follows the `pengyos-updates` rules exactly: one notification a day per thing,
  a snoozed set stays quiet, a newer update is news, off means off.
- **Privilege stays where the repo puts it.** The app is unprivileged: checking
  and installing are `pengyos-firmware-check.service` (daily timer) and
  `pengyos-firmware-update@<ids>.service`, started through `systemctl`, with
  `60-pengyos-firmware.rules` letting wheel at an active local session start them
  without a password. The install unit runs `fwupdmgr` with the prompts answered
  and **never restarts the machine**; writing firmware is irreversible, so it
  happens only after a person presses the button.
- **`fwupd.service` is now enabled** in the Desktop overlay. `BR2_PACKAGE_FWUPD`
  has been in the Desktop layer for a while, and Buildroot's recipe installs the
  unit files and enables none of them, so the daemon was not running and
  `fwupdmgr` had nothing to talk to — the fleet's firmware work has all been done
  from a terminal, which is how the gap stayed invisible.
- Wiring is enforced, not remembered: `scripts/tests/test_cosmic_app_wiring.py`
  now requires the app in the cargo list, the post-build install, the launcher,
  the notifier autostart entry, the three units, the `fwupd.service` and
  `pengyos-firmware-check.timer` enablement symlinks (the timer under
  `timers.target.wants`, matching its `[Install]`) and the polkit rule to agree.
- **Installing works at all.** The binary passed its own `apply` subcommand to
  fwupdmgr as a device id, so every install — "Update all" included — failed
  with `'apply' is not a valid GUID nor DEVICE-ID`. Found under QEMU.
- **Installing on more than one device at once works.** The app joined the device
  ids with commas into the unit's instance name, and systemd refuses a comma in
  a unit name, so "Update all" worked and installing a chosen set did not start.
  The app now starts the `systemd-escape`d name (`\x2c`) and the unit passes the
  unescaped `%I` on. Both units keep `/run/pengyos-firmware` on exit
  (`RuntimeDirectoryPreserve=yes`), so a daily check finishing mid-install no
  longer deletes the progress file under the app, and fwupdmgr's stderr goes to
  the journal instead of a pipe nothing drained.

### Added (26.10.30) — Disks can partition a hard drive, not just format a stick
- `pengyos-disks` 0.2.0 grows from the USB-stick tool into the Desktop's disk
  utility, the GNOME-Disks-shaped job the Desktop was missing. Every drive
  (internal, external, USB, memory card) is in a sidebar; the chosen one shows its
  partition table and a bar of its partitions and free space in proportion. Over
  UDisks2, with no root and no new polkit rules: *New partition table…* (GPT/MBR),
  *Create partition…* in free space (a size or all of it; ext4, btrfs, XFS, FAT32,
  exFAT, NTFS, swap or unformatted; a name), *Format…*, *Grow…* (a partition and
  its filesystem into the space after it), *Delete…*, *Mount*/*Unmount*, swap
  on/off. The 0.1.0 stick flows (format, write an image, eject) are unchanged.
- **The running system cannot be broken from the window.** Whole-drive actions are
  refused on a drive the system has something mounted from or swapping on. A
  partition is locked when the system has it mounted, it is active swap or
  read-only, or, on the drive the system runs from, it is one of PengyOS's own
  (`PENGYOS-ESP`, `pengyos-root-a/b`, `pengyos-data`, `PENGYOS-PAYLOAD`) or an EFI
  System Partition or x86-64 root by type — mounted or not, since the inactive root
  slot never is and the installer stick's ESP is just called "boot". The same
  partitions on another, unused disk can be erased. `80-pengyos-udisks-hide.rules`
  stays: `UDISKS_IGNORE` only sets `HintIgnore`, so COSMIC Files still hides those
  partitions while Disks shows them, locked (checked in QEMU).
- Sizes, names and each filesystem's smallest size are checked before anything is
  written; every destructive step names what it erases and needs a red button.
  `--list` prints the layout and the locks. 38 unit tests, no display, no D-Bus.
- QEMU, Desktop image, live stick + a blank 20 GB virtio disk, all from the
  window: new GPT table; a 5 GB ext4 partition created, mounted, grown to fill the
  disk while mounted (a file on it survived), reformatted as btrfs (unmounted
  first), deleted; a 2 GB swap partition and exFAT in the rest, swap turned on
  (partition and drive then locked) and off; then an MBR table and a partition on
  it (type `0x83`). The stick is locked whole, its "boot" ESP locked by type. Not
  yet run: an installed system as a non-root user (the polkit prompts for an
  internal disk).
- Rebuilt from scratch on 2026-10-16: the first 0.2.0, written by a PengyTasker job
  on 2026-10-15, was thrown away uncommitted by the nightly cron (see
  `pengyos-nightly`'s CHANGELOG); only this entry had been committed.

### Added (26.10.30) — Disk Usage: what is taking the space, as a ring chart
- `pengyos-usage` 0.1.0, *Disk Usage* in the App Library: the native Desktop app
  for "where did my space go" (GNOME's Disk Usage Analyzer, for COSMIC). Places
  lists the home folder and each filesystem once, with how full it is; a scan
  (parallel, stoppable, live counts) shows the folder as a four-ring chart you
  click into, beside a ranked list with shares; crumbs, *Open in Files*, *Scan
  again*. Read-only.
- The numbers match `du -s -B1` to the byte: disk usage, hard links once,
  symlinks not followed, other filesystems not walked (by mount point, so btrfs
  subvolumes are walked), btrfs `.snapshots` not counted (they share blocks with
  what they copy), unreadable folders noted. A 12.4-million-file `/data` in 2 m 44 s.
- `pengyos-storage` (below) stays as the terminal/HTML version for Server and
  scripts.

### Added — `pengyos-storage`: where the disk went, as a picture
- A new toolkit tool for the question `du` answers badly: **what is taking the
  space**. `pengyos-storage scan /data -f html -o report.html` walks the tree and
  writes **one self-contained page** — inline CSS, inline SVG, no JavaScript and
  no network references — with a donut of the top entries, a squarified treemap
  where rectangle area is bytes, ranked tables per depth, and the top individual
  files. `scan` on a terminal prints the same ranking as bars; `du` is the
  `du | sort -n` replacement (sorted biggest-first, with share bars); `disks`
  lists every filesystem with a used-space bar.
- **The numbers are the disk's, not the file's**: disk usage (`st_blocks*512`)
  by default with `--apparent` for `st_size`, hard links counted once, symlinks never followed,
  `--one-file-system` reporting a mount point instead of walking it, and
  `--exclude` space reported as excluded — `total = listed + below-the-cut +
  excluded` is printed, so nothing vanishes between the scan and the report.
  Read-only throughout: it never deletes, moves or compresses anything.
- 118 tests, no root and no network: the walker takes an injectable `scandir`,
  so hard links, foreign devices, permission errors and the chart geometry
  (slices summing to 360°, non-overlapping area-proportional rectangles) are all
  checked offline. Measured on behoserver: 472 GiB / 11.4M files / 956k
  directories in 3m16s, a 15 KB page.
- Agent-authored (Pengy, on behoserver) from the operator's request. Not wired
  into any image and not in the `pengyos` meta-CLI's subcommand list yet — that
  is a follow-up, not part of this change. (An earlier version of this entry said
  counting hard links once keeps btrfs snapshots from counting double; it does not —
  snapshots are not hard links. `pengyos-usage` skips btrfs `.snapshots` instead.)

### Fixed (26.10.29) — the dev box's cargo and uv were installed and not on the PATH (found on behocraptop running 26.10.27)
- **`pengyos-guest enter dev` gave a zsh with no cargo.** The dev box installs
  rustup into `~/.cargo/bin` and exported the PATH through
  `/etc/profile.d/pengyos-dev.sh`, which only **bash login shells** read. PengyOS's
  default shell is zsh, and Ubuntu's zsh never sources `/etc/profile` — it reads
  `/etc/zsh/zshenv`, `zprofile` and `zshrc`, none of which touch `profile.d`. So
  the box came out with `cargo`, `rustc` and `uv` installed and none of them
  findable, while `gcc`, `git` and `go` (apt packages, in `/usr/bin`) worked, and
  `uv` worked only by accident because the shared `~/.zshrc` adds `~/.local/bin`.
- **One PATH file, sourced by both shells.** `guests.d/dev.toml` and
  `guests.d/cuda.toml` now write `/etc/pengyos-dev-path.sh` and have
  `/etc/profile.d/pengyos-dev.sh` source it for bash, and append a guarded
  `. /etc/pengyos-dev-path.sh` line to the box's `/etc/zsh/zshenv` for zsh.
  `zshenv` is the only system zsh file sourced on *every* invocation, so it
  covers both `enter dev` and `enter dev -- cargo build`. The append is guarded
  by a marker so re-applying the guest does not accumulate lines. The box's own
  `/etc` is what changes — the shared `~/.bashrc`/`~/.zshrc` stay untouched, so
  the host PATH is unchanged. Both boxes now install `zsh` with their packages,
  so `/etc/zsh/zshenv` belongs to the package before the line is appended (a
  later `apt install zsh` would otherwise meet a config file it does not own),
  and the PATH file skips the export when `~/.cargo/bin` is already on the PATH.
- **Locked by a test.** New `tools/pengyos-guest/tests/test_guests_d.py` tests
  the shipped `guests.d/*.toml` as the deliverable (post-build copies them
  verbatim) and asserts any guest that puts a toolchain on the PATH wires it for
  both shells, from one shared file, idempotently.

### Fixed — installing offline and joining wifi afterwards left the Store with no sources and Store apps out of every menu (found on a laptop running 26.10.27)
- **The COSMIC Store listed no sources.** `pengyos-flatpak-setup.sh` ignored a
  failed `flatpak remote-add flathub` (no network on the first boot) and still
  wrote its `.pengyos-setup` marker, so it never ran again and the machine had
  no system Flathub. It now fails without the marker and retries
  (`Restart=on-failure`).
- **Apps installed from the Store appeared in no menu.** With no system remote,
  the sources added by hand were per-user, so the Store installed into
  `~/.local/share/flatpak`. `start-cosmic` set `XDG_DATA_DIRS` to the system
  export folder only (flatpak's `profile.d` and user environment generator
  don't reach a greetd session). It now puts the user's export folder first and
  creates it before the panel scans icons.
- **Firefox arrived up to 10 minutes after joining wifi, or only after a
  reboot.** New NetworkManager dispatcher hook
  `/usr/lib/NetworkManager/dispatcher.d/50-pengyos-flatpak-setup` starts both
  first-boot units once a connection is up. It uses `restart` on a unit
  waiting out `RestartSec`, because systemd holds a `start` behind the restart
  timer, and it leaves a running unit alone.

### Fixed (26.10.26) — blank buttons across COSMIC: the icons it leaves to the Pop theme were never installed (found on behoweather running 26.10.25)
- **The COSMIC Store's Updates page showed an empty square next to every
  Update button** (the release-notes button, `help-info-symbolic`). COSMIC's
  icon theme draws most icons itself and inherits **Pop** for the rest; the
  image installed only Pop's cursors (`pop-cursor-theme`), with a Pop
  `index.theme` listing no icon directories, so every icon COSMIC expects from
  Pop was missing and libcosmic drew nothing.
- **An audit of every icon name the pinned COSMIC sources request** (literals
  across lines, `match` arms, formatted names; icons the apps embed through
  their `icon_cache` excluded) found 11 in use and in no installed theme, all
  in Pop: the Store button above, `link-symbolic` (every app's About page),
  Files' grid/list toggle, the VPN icon (network applet and Settings), Player's
  repeat-mode and subtitle buttons, the Displays and Date & Time page icons in
  Settings, and the "touchpad disabled" OSD. The same audit found nothing
  missing in the PengyOS apps, launcher icons (besides two GeoClue demo
  launchers, also fixed by Pop), MIME defaults, D-Bus services, unit
  `Exec` paths or event sounds.
- `pop-cursor-theme` now also installs Pop's `scalable/` set (584 symbolic SVGs,
  2.3 MB; not its 68 MB of fixed-size PNGs) and lists it in Pop's
  `index.theme`. COSMIC's own icons still win wherever both have one. The
  Desktop post-build fails if any of the 11 is in no theme again
  (`scripts/tests/test_cosmic_icon_theme.py` pins the recipe and the check).

### Fixed (26.10.25) — what the review of the 2026-10-12 batch found before it shipped

- **The comfort shell broke the commands it aliased:** `grep`/`fg` ran a
  nonexistent `ripgrep`, `fg` lost job control, `diff` ran an unshipped
  `delta`, root's `ls` failed on an eza flag that does not exist, and
  `cat`/`grep` forced colour into pipes. Its run-once markers were exported, so
  tmux panes and nested shells skipped it, and the starter `~/.zshrc` printed
  `no such option: hist_substring_search` on every start and then overwrote the
  starship prompt. Details in `tools/pengyos-server-toolset/CHANGELOG.md`.
- **Gamepads' Rumble could not have worked** (wrong node, wrong ioctl numbers,
  wrong struct, never started), `EVIOCGABS` was misnumbered, and the sysfs
  bitmaps were read as 32-bit words, so a mouse listed as a keyboard. The
  kernel fragment's `CONFIG_INPUT_FF_MEMSET` named a symbol that does not
  exist. Details in `tools/pengyos-gamepads/CHANGELOG.md` (0.1.1).
- **Gamepads, tested with a real pad (8BitDo, xpad):** the live panel never
  moved (events were queued and never drained), 15 input codes were wrong
  against the kernel header (the D-pad showed as stray bars), and the window
  listed every keyboard and audio jack above the pad. 0.1.2 fixes all three
  and lays the panel out like an Xbox pad, with a no-overlap test; buttons
  and rumble confirmed by hand on behoweather.
- **Seeding the Pengy config left `~/.config` owned by root** on Server and
  Core, whose skeletons have no `~/.config`.
- `board/desktop/post-build.sh` had lost its execute bit in the working tree.
- **The comfort shell's fzf never loaded, and the `fzf` recipe never built.**
  The recipe installed `$(@D)/fzf` (the golang infra builds `$(@D)/bin/fzf`),
  the profile's fzf snippet probed for a `key-bindings.sh` no fzf ships, and
  `FZF_DEFAULT_OPTS` defaulted to `-height …`, which fzf rejects.
- **The starship theme did not parse**: every prompt printed
  `Error parsing "format"` (seen on the first QEMU boot of the review build).

### Added (26.10.25) — Printers: add a printer, manage the queue and print a test page without a browser
- **Managing printers meant CUPS' web pages.** COSMIC Settings (epoch 1.9) has
  no printer page, so the "Printers" launcher opened `localhost:631` in a
  browser: a password prompt, CUPS' vocabulary, and no way to see the queue
  from the desktop (US-DSK-13).
- **`tools/pengyos-printers`** (*Printers*, MIT, libcosmic, host-built like
  Disks and Gamepads, in `scripts/build-cosmic.sh`'s app list): the printers
  with status in words and supply levels, Print test page, Pause/Resume, Make
  default and Remove…; the print queue with Cancel, Hold/Resume and Cancel all;
  the last few finished documents. **Add printer…** lists what CUPS finds on USB
  and the network (one entry per printer, driverless first) or takes an
  address, and recommends the driverless (IPP Everywhere) driver whenever the
  printer supports it, else the drivers matching its device ID or model, with a
  search over all ~5,500. It speaks IPP to cupsd's local socket with peer
  credentials, so wheel members change printers with **no password**; others
  can look and are told they cannot change anything.
- **The old launcher is gone** (`pengyos-printers.desktop` →
  `io.catbee.pengyos.Printers.desktop`, with an "Add a printer" action), and
  the Desktop post-build deletes it from existing build trees.
- Tested on behoweather against the network Brother MFC-L3720CDW and a
  throwaway `socket://` queue: add (driverless and a model driver), default,
  pause, test page, hold, release, cancel, resume, remove, and a non-wheel user
  refused. It found one real bug in the obvious approach: the image's test page
  is cups-filters' PDF banner, and declaring it as CUPS' own banner format is
  refused ("Unsupported format"), so the format is read from the file.

### Changed (26.10.25) — the userland agents keep tripping over is the real one: Rust coreutils, and no more cut-down applets

Agents on PengyOS kept behaving as if the box were an initramfs: `find -printf`
rejected, `grep -P`/`--include` unknown, `sed` with no `\b`, `awk` with no
`gensub`, `xargs -d`, `diff -r`, `time -v`, `unzip -l`, `killall -q`,
`losetup -f`, `last -x`, `su -l`. Auditing the built images explains it: the
Server rootfs carried **144 BusyBox applets**, and Buildroot resolves the
BusyBox/real-tool overlap by *install order* (`BUSYBOX_DEPENDENCIES` lists
coreutils, grep, tar, … so they build after BusyBox and their files win). Every
applet no package claims just stays — cut down. In an image with **no initramfs**
at all, that is pure embedded-systems inheritance.

- **Coreutils are Rust now:** `package/uutils-coreutils` (our recipe; Buildroot
  2026.08 ships no uutils recipe at all) is pinned to uutils/coreutils 0.11.0,
  sha256-verified against the live tarball, and built
  `--no-default-features --features unix` — the 78 common-core utilities *plus*
  the unix-only ones GNU coreutils ships on Linux (stat, id, sync, uname, whoami,
  install, stty, timeout, hostname, groups, nice, nohup, chroot, chgrp, chmod,
  chown, nproc, mkfifo, mknod, logname, hostid, users, who, uptime, pinky, stdbuf,
  kill, arch). One multicall `/usr/bin/coreutils` with a symlink per utility, 106
  names. GNU coreutils (GPL-3) is out; MIT is in, matching Ubuntu 25.10.
- **BusyBox applets are resolved in the config, not by luck:**
  `board/core/busybox.fragment` (+ `board/server/busybox.fragment`) compiles out
  every applet another package owns, plus the early-boot/embedded set
  (linuxrc, run-init, pivot_root, switch_root, makedevs, mdev, uevent, i2c-*,
  fdformat, setserial, devmem, ubirename, console plumbing) and the daemons the
  image does not run (telnet, tftp, inetd, dnsd, ssl_server, udhcpc, syslogd,
  klogd). Merged config drops from 563 to 369 enabled symbols.
- **The tools behind those flags are installed where they are cheap:** Core gains
  findutils, grep, sed, gawk, GNU time, which, diffutils, patch, gzip, Info-ZIP
  unzip, tar, tree, lsof, less, attr, psmisc and the util-linux losetup, last,
  eject, su, mesg, hwclock, schedutils, fallocate, mountpoint, logger; Server
  gains net-tools, iputils, traceroute, pciutils, usbutils, hdparm and zip.
  BusyBox's `wget` gets `FEATURE_WGET_OPENSSL` so HTTPS verifies.
- **`/bin/sh` is bash.** Agents write bash-isms into `sh -c` — `[[ ]]`, arrays,
  `shopt`, `local -a`, process substitution — and BusyBox ash accepts some of
  them and silently misbehaves on the rest.
- **Tests with teeth:** `scripts/tests/test_core_userland.py` (10 checks) fails if
  a fragment names a symbol BusyBox 1.38 does not declare (a typo is a silent
  no-op), if an applet is removed with no selected package providing the command,
  if GNU coreutils comes back, if a fragment is dropped from a generated
  defconfig, if a layer sets a symbol Buildroot 2026.08 does not define, or if the
  recipe's utility list loses a name Core already shipped.
  `tools/pengyos-recipe-uutils` now pins the `feat_os_unix` fixture (106 names)
  and fails if the tree's copy of the recipe drifts from the authored one.

### Added (26.10.25) — the Server's shell stops feeling stock: the comfort profile is wired in, and zsh gets the oh-my-zsh feel

`zsh` was the installed user's shell with a bare starter `~/.zshrc`, while the
comfort set the Server promises (starship prompt, fzf, zoxide, the modern
aliases, tmux defaults) sat in `tools/pengyos-server-toolset/` **authored but
never connected to the image** — the layer enabled none of its packages, the
profile was never installed, and nothing sourced it. Found 2026-10-12 while
answering "why does our zsh feel stock compared to oh-my-zsh?". The answer is
not "install oh-my-zsh" (a git-checkout framework with an update ping has no
place in a reproducible distro); the curated equivalent is now wired:

- **Recipes wired:** `fzf` (Go) and `starship` (Rust) joined
  `buildroot-external/package/` (Buildroot 2026.08 ships neither), plus two new
  pure-shell recipes — `zsh-autosuggestions` 0.7.1 and
  `zsh-syntax-highlighting` 0.8.0 → `/usr/share/zsh/plugins/`. All four are
  enabled by the Server layer (so Desktop inherits them) and their sources are
  hash-verified against the tree (`make …-source` green; starship's digest
  re-pinned to the `-cargo6` repack Buildroot 2026.08 downloads).
- **The profile installs and is sourced system-wide:** the Server post-build
  copies the toolset's `shell/` to `/etc/pengyos/shell/` (tmux defaults →
  `/etc/tmux.conf`); the overlay's `/etc/zshrc` (plus an `/etc/zsh/zshrc`
  symlink — Buildroot's zsh reads the former, Debian-patched zsh the latter)
  and `/etc/profile.d/pengyos-shell.sh` source it; `/etc/skel/.zshrc` and a new
  `/etc/skel/.bashrc` source it for new accounts, and stay useful standalone on
  Core.
- **The starter `~/.zshrc` gained the plugin-free oh-my-zsh bits:**
  complete-in-word, cached/grouped/colored completion,
  git aliases — and it keeps the `%`-prompt fix (logind's CAP_WAKE_ALARM makes
  zsh's `%#` show `#` for a wheel user).
- **Fixed in the profile:** `starship init sh` emits nothing (init is
  shell-specific), so the prompt never loaded — the profile now detects
  zsh/bash, passes it to `starship init`, and points `STARSHIP_CONFIG` at the
  system theme when nothing else claimed it. The copied fzf/starship recipes
  also carried spaces inside `$(call github, …)`, which Buildroot puts
  verbatim into the download URL (404s).
- **Drift-proof:** `tools/pengyos-server-toolset` gained image-wiring,
  starship-wiring, and under-zsh runtime tests (the profile sources cleanly and
  its idempotency guard fires on a double source); the gate gained
  `scripts/tests/test_server_comfort_shell.py` (layer symbols, recipe wiring,
  generated defconfigs, rc wiring, `sh -n`/`zsh -n` on the profile).

### Added (26.10.25) — Pengy arrives configured: the installer asks for its endpoint, and the image ships skills

An installed PengyOS has always *had* Pengy and never *known* Pengy: the agent
shipped with a blank config (local endpoint, no model), and `~/skills/` did not
exist, so the first chat was a stranger with no tools pointed at anything.

- **The install wizard has a Pengy page** — base URL, API key, model — in both
  front ends: a screen in the Desktop installer (`pengyos-install-gui`) and a
  page in the console installer (`pengyos-install-tui install`). It is
  **skippable by design**: Enter past it, and Pengy keeps the defaults the image
  ships. Plenty of people have no endpoint at install time, and an install that
  demands one is an install that lies.
- **The engine seeds the config.** `pengyos-install image` takes
  `--pengy-base-url` / `--pengy-api-key(-stdin)` / `--pengy-model` and writes
  `~/.config/pengy/settings.json` for the user it creates — mode 0600, owned by
  them, key on stdin so it never appears in argv. Only the four answered keys
  are written; Pengy merges its defaults over the rest.
- **The image ships a skills tree.** `/etc/skel/skills/` now carries
  `skill_index.md` plus two skills — `pengyos` (how this OS works, where
  everything lives, the four lanes for installing software) and `pengy` (what
  the agent is and how to drive it) — so every new user's `~/skills/` starts
  populated, from `useradd -m` like the rest of the skeleton.
- **The system message names the skills.** The default reads *"You are a helpful
  assistant named Pengy you have tools and skills. The current date is {date}
  and the user is {username} on host {hostname} which is {osinfo}. Always check
  ~/skills/skill_index.md before doing anything to see if a skill can help with
  the task."* PengyR's own default is the same sentence; the installer writes it
  explicitly so an image built against an older PengyR still introduces the
  agent to its skills on the first chat.

### Added (26.10.25) — Gamepads: the Desktop can now see its controllers and test them
- **A controller was invisible until a game opened it.** COSMIC Settings
  (epoch 1.9) has no page for gamepads, joysticks or any controller: checking a
  stick calibration, a dead button or a stuck trigger meant launching Steam and
  finding a title with a pad setup screen. The kernel drivers, `steam-devices`
  and GameMode were all in place since 26.10.x — US-DSK-4 accepts that
  "controllers are correct" — and nothing showed them.
- **`tools/pengyos-gamepads`** (*Gamepads*, MIT, libcosmic, host-built like
  Disks, in `scripts/build-cosmic.sh`'s app list): every `/dev/input` device
  with its kind, bus, `vendor:product` and capability summary, the game
  controllers headlining the list, and a live panel of the selected one —
  buttons light up, stick knobs follow, trigger/pedal/steering bars fill, the
  D-pad points, each axis prints its raw value and the extremes seen since it
  was selected, and every event is logged. A Rumble button sends a real
  `FF_RUMBLE` effect. `--list` / `--json` answer with no window (sysfs only, so
  over ssh too), `--selftest` animates a complete synthetic pad.
- **The permission gap that made this necessary was real, and is fixed.**
  udev gives every `/dev/input/event*` to group `input` at mode 0660; the image
  defined that group only as a side effect of the greeter's supplementary
  groups, so a desktop user could not read a controller at all (Steam reaches
  its pads through `steam-devices`' `TAG+="uaccess"` ACLs, which is why Steam
  never noticed). `input` is now an explicit row in `board/desktop/users.table`,
  the installer adds the owner (it was already in `USER_GROUPS`), and
  `merge-accounts` adds existing owners at the next boot.
- **Rumble goes through the pad's event node.** Force feedback is an evdev
  interface: upload a `FF_RUMBLE` effect (`EVIOCSFF`), start and stop it with
  `EV_FF` writes, erase it (`EVIOCRMFF`). The event node is already the
  `input` group's at 0660, and the pad drivers select `INPUT_FF_MEMLESS`, so
  the image needed nothing new for it. `joydev` is loaded at boot
  (`usr/lib/modules-load.d/pengyos-joydev.conf`) for the legacy
  `/dev/input/jsN` API older games read, and `90-pengyos-joystick.rules`
  gives that node the event nodes' group and mode instead of systemd's
  world-readable 0664.
- **Measured:** 25 unit tests with no display and no `/dev` (the ioctl numbers
  and `ff_effect` layout checked against `<linux/input.h>`; the classification
  of eleven device shapes — a mouse never reads as a joystick, a wheel is not
  drawn as a gamepad; the sysfs capability-bitmask round trip and its word
  order; the event decoder; the live state, axis normalisation over the device's
  own `EVIOCGABS` range and the D-pad folded from both of its axes; the panel
  layout; the modalias/bus parsing; the list and JSON formats). Live on
  behoserver: 18 event nodes, no controller attached, the audio jacks classified
  as Audio and the power buttons as Other, the headline line reading
  `no game controllers`. A pad attached under QEMU (`virtio-input-host`) is the
  next check; the panel is exercised with no hardware by `--selftest`.

### Added (26.10.25) — The rest of the everyday file types get their default apps

- **The archive fix was one instance of a class, so the whole overlay was
  audited.** Measured on behoweather (26.10.24) with the image's *own* resolvers
  (`xdg-mime`, `gio`) over ~150 everyday extensions, mapped to canonical types
  through the image's `shared-mime-info` `globs2`, and scored against the app set
  the Desktop image actually ships (COSMIC Files/Edit/Viewer/Player/Store, mdit,
  Pengy, Disks, Firefox — user-installed Flatpaks like VLC excluded, because they
  mask gaps a fresh image has). Four distinct causes turned up, all of them
  registration, not missing capability:
  - **`xdg-mime`/`xdg-open` match the type string exactly; `gio` does not.** gio
    walks shared-mime-info's inheritance tree, so a type with no row of its own
    still resolves through its super type — COSMIC Files' *Open with* found
    CosmicEdit for `.csv`, `.py`, `.css`, `.srt`, while `open data.csv` answered
    "no method available for opening". Every `text/plain` descendant is in this
    class, which is why the overlay's single `text/plain` row looked complete.
  - **COSMIC Player's `.desktop` declares video only** (its `Categories` say
    `AudioVideo`), so audio files had a handler only where the hand-maintained
    overlay happened to list one — and *Open with* never listed Player for audio.
    Same shape as the cosmic-files archive gap.
  - **Legacy vs canonical type names.** The overlay's icon row is `image/x-icon`;
    `shared-mime-info` maps `.ico` to `image/vnd.microsoft.icon`, which xdg-mime
    cannot match.
  - **Disks declares three types and nothing made it the default** — a downloaded
    `.iso`/`.img` had no handler, one commit after the app that writes them landed.
- **Landed:** 39 rows in the overlay's `mimeapps.list` (the `text/plain` family,
  disk images → Disks, the images Viewer decodes, the audio Player decodes) plus
  two `.desktop` patches in `cosmic-patches/`:
  `cosmic-player/0001-…-declare-the-audio-mime-types-cosmic-player-decodes.patch`
  (19 audio types the image's GStreamer plugins demux: ogg/vorbis/opus, isomp4,
  flac, wavparse, mpg123 + audioparsers, plugins-bad's aiff) and
  `cosmic-viewer/0001-…-declare-the-image-mime-types-cosmic-viewer-decodes.patch`
  (`dds`, `x-exr`, `x-tga`, `vnd.microsoft.icon` — decoders the Rust `image`
  crate enables through `default-formats`, which cosmic-viewer does not disable).
- **Measured, not asserted:** applying the overlay + both patches and rebuilding
  `mimeinfo.cache` resolves **38 previously unopenable extensions, with 0
  regressions** (the 6 archive rows in that count are the entry below). Every new
  row was checked with `xdg-mime query default` under the session's
  `XDG_DATA_DIRS`. `desktop-file-validate` reports the *same* findings on the
  patched `.desktop` files as on the pristine upstream ones — the two patches
  introduce none.
- **Deliberately left uncovered, with the reason.** `.docx/.xlsx/.pptx/.odt/
  .epub/.cbz/.jar` resolve today only because they are zip containers, so the
  only app available to point at is the file manager — extracting a document
  instead of reading it is worse than no default, and the real answer (LibreOffice
  / Evince) is the curated set, i.e. an operator ratification (spec Principle 8),
  not an agent's call. `.7z`, `.rar`, `.zst`, `.lz4`, `.lzip` have no decoder in
  the image; `.psd` and `.xcf` are not decodable by cosmic-viewer (the `image`
  crate's `psd` feature is not enabled); `.mid`/`.mod` need a synth and a module
  player, neither shipped; fonts, `.djvu`, `.torrent`, `.exe`/`.msi`/`.apk` have
  no app in the image at all. A row naming an app that cannot open the file is a
  worse bug than the gap it closes.

### Added (26.10.25) — Archives open: zip / tar / gzip / bzip2 / xz now have a default app

- **A downloaded `.zip`, `.tar.gz`, `.tar.bz2` or `.tar.xz` had nothing to open
  it.** Opening one from a browser's download row, from `open foo.zip`, or from
  any app outside COSMIC Files warned that no application handles that file type
  (`xdg-open`: "no method available for opening"). Two independent gaps, both
  measured on behoweather (26.10.24): the system `mimeapps.list` had **no
  archive rows at all**, and `mimeinfo.cache` had **no archive handler** —
  COSMIC Files' `.desktop` declared only `MimeType=inode/directory;`.
- **The capability was already in the image; only the wiring was missing.**
  COSMIC Files extracts zip, tar, gzip, bzip2 and xz itself (`src/archive.rs`,
  `SUPPORTED_ARCHIVE_TYPES`; `bzip2` and `lzma-rust2` are default features, so
  the shipped binary has them), and a double-click *inside* COSMIC Files already
  routes a supported archive to *Extract Here / Extract To* — it never asks
  xdg-open. So the fix is registration, not a new app:
  `cosmic-patches/cosmic-files/0001-declare-the-archive-mime-types-cosmic-files-opens.patch`
  declares the nine types it supports (so *Open with* lists it and
  `update-desktop-database` indexes it), and the overlay's `mimeapps.list` makes
  it the system default for them. Verified with a user-level `mimeapps.list`:
  `xdg-mime query default application/zip` (and tar, x-compressed-tar, bzip2,
  xz) resolves to `com.system76.CosmicFiles.desktop`.
- **Not covered, deliberately:** `.7z` and `.rar` have no handler — cosmic-files
  does not read them and the image ships no `p7zip`/`unrar`. A GUI that *browses
  into* an archive, creates archives of its own, and covers 7z/rar is File Roller
  (`org.gnome.FileRoller`), which is already the human-ratified `archive` entry
  in `tools/pengyos-desktop-curation/curated.toml` — see the note below.
- **The curated default-app set is not wired into any build.**
  `pengyos-desktop-curation` emits a `mimeapps.list` from `mime-integration.toml`
  (whose `archive` entry is File Roller with zip/gzip/tar/bzip2), but nothing in
  `buildroot-external/` consumes it, no Flatpak is installed at first boot
  (`pengyos-flatpak-setup.sh` only adds the Flathub remote + appstream), and the
  set's GNOME apps (Files, Text Editor, Evince, File Roller) are not the apps the
  Desktop image ships (COSMIC Files/Edit/Viewer/Player). The overlay's
  `mimeapps.list` is hand-maintained, which is why it had image/text/video rows
  and no archive rows. Ratifying the curated set against the shipped app set is
  an operator decision (spec Principle 8), not an agent one.

### Added (26.10.25) — Disks: format USB sticks and memory cards, and write disk images to them

- **Nothing graphical could format a drive.** The Desktop had UDisks2,
  polkit and every mkfs tool (FAT, exFAT, NTFS, ext4), but COSMIC Files only
  mounts and ejects, and COSMIC has no Disks app; formatting a stick or making
  an installer stick from a running PengyOS needed `parted`/`mkfs`/`dd` in a
  terminal. New app **Disks** (`tools/pengyos-disks`, libcosmic, our own, MIT):
  Format… (one partition, FAT32 / exFAT / NTFS / ext4, a name), Write image…
  (`.img`/`.iso`, or the `.img.xz` the download page publishes, unpacked on the
  fly, with progress and Stop), and Eject. It is in the App Library and is
  offered by *Open with* for disk images. Only removable drives are listed;
  drives the system has mounted (such as the live stick), active swap and
  write-protected cards are locked. All work goes through UDisks2; no polkit
  rule was added: formatting and ejecting need no password (UDisks' policy for
  the active user), writing an image asks for the administrator's password.
- **exFAT labels could not be set at all.** glibc's UTF-16 converter is a gconv
  module and the Desktop image shipped none, so `iconv_open("UTF-16LE", …)`
  failed; libblockdev's exFAT label check converts to UTF-16, so UDisks refused
  every exFAT name ("must be a valid UTF-8 string") — after it had already
  rewritten the partition table. The desktop layer now copies glibc's Unicode
  gconv modules (UTF-16, UTF-32, UTF-7, UNICODE; 92 KB; not the 8.3 MB of
  legacy codepages). Found testing Disks.

### Added: realtime scheduling, location, a virtual camera; fixed: rootful containers had no network (persona audit, 26.10.23)

An audit of the Desktop against a mixed household (gamer, developer, audio,
photo/video, streamer, power user, a grandparent who prints PDFs) for services
that apps expect the OS to provide.

- **The Realtime portal had nothing behind it.** xdg-desktop-portal offered
  `org.freedesktop.portal.Realtime` and forwarded every request to RealtimeKit,
  which the image did not ship ("Failed to load RealtimeKit property" at every
  session start). Flatpak audio workstations, games, voice chat and PipeWire
  inside a sandbox ran without realtime threads. New recipe `package/rtkit`
  (0.13, D-Bus-activated, `rtkit` user).
- **There was no Location portal at all.** xdg-desktop-portal compiles it in
  only against libgeoclue, and the meson option was `auto`. Maps, weather apps,
  browsers and COSMIC Settings' sunset-based dark mode ("A portal frontend
  implementing org.freedesktop.portal.Location was not found") had no
  location. New recipe `package/geoclue` (2.8.2), with Wi-Fi lookups in
  BeaconDB (upstream's default since Mozilla Location Service closed; only
  when an app asks and the portal has allowed it; no data submission) and
  ModemManager's GPS/cell sources. `xdg-desktop-portal` is now built with
  `-Dgeoclue=enabled`. Geoclue's demo agent autostarts with the session so
  clients don't wait 5 s for one. Two more things stood between an app and a
  location, both found by booting it:
  - The portal still answered "Location services disabled". Its Lockdown
    backend (xdg-desktop-portal-gtk) reads GNOME's
    `org.gnome.system.location enabled`, which defaults to false; GNOME's
    Privacy page flips it, but COSMIC has no such switch. A GSettings override
    (`board/desktop/gsettings/`, installed into staging by `external.mk` so
    Buildroot's schema compile picks it up) turns it on. Each Flatpak app is
    still asked once.
  - **No GIO TLS backend.** Without glib-networking, every libsoup/GIO client
    failed HTTPS with "TLS support is not available": geoclue's lookups, and
    gvfs's dav/http backends (COSMIC Files' `davs://`, e.g. Nextcloud). The
    Desktop now ships glib-networking.
- **OBS's virtual camera could not start.** It needs the v4l2loopback kernel
  module, which a Flatpak cannot bring. Desktop now builds it; OBS loads it on
  demand, with OBS's own options set in
  `/usr/lib/modprobe.d/60-pengyos-v4l2loopback.conf`.
- **Rootful podman containers had no network (Server and Desktop).** The
  firewall's input chain dropped DNS queries to aardvark-dns on the podman
  bridge, and its forward chain (policy drop) dropped container traffic and
  ports published with `-p`, whatever netavark's own table accepted. On top of
  that, `nftables.conf` began with `flush ruleset` and the unit stopped with it,
  so reloading or restarting the firewall deleted netavark's table and cut off
  every running container. The ruleset now replaces only `table inet pengyos`.
  It accepts input from `podman*` bridges, and forwarded traffic that is
  established, from a podman bridge, or DNATed (a published port). The unit
  stops by deleting only our table and gained `ExecReload`. On Core/Server a
  second cause sat underneath: the wired `.network` matched `Type=ether`,
  which includes podman's host-side veth. networkd took that veth over (DHCP,
  per-link forwarding off), so containers had no route out even with the
  firewall open. The match now adds `Kind=!*`, physical NICs only, as
  systemd's own `89-ethernet.network.example` does. That also keeps it off
  bonds and VLANs. Verified in QEMU: container egress works, and a port
  published with `-p` answers from outside through an nftables reload and a
  restart. Rootless podman (pasta) was never affected.

### Changed: a desktop firewall, icon panel buttons, click-to-open notifications, mdit for Markdown

- **The Desktop firewall opens ports above 1024**, as Fedora Workstation's zone
  does. The Desktop had inherited the Server's default-deny, so Steam Remote
  Play and LAN games, Minecraft "Open to LAN", LocalSend, KDE Connect and
  Syncthing's direct connections failed silently. Privileged ports stay closed
  except ssh. `/etc/nftables.conf` now includes
  `/usr/lib/pengyos/nftables.d/input-*.nft`; the Desktop ships
  `input-desktop.nft` and the Server ships none.
- **Workspaces and Applications on the top panel are icons**, not COSMIC's
  default text (`/usr/share/cosmic/com.system76.CosmicPanelButton/v1/configs`).
  This is a deliberate divergence from Pop!_OS.
- **Clicking a notification opens the app it came from** when the notification
  offers no action. COSMIC only dismissed it; GNOME (Ubuntu, Fedora) opens the
  app named in the desktop-entry hint. New cosmic-notifications patch `0002-…`.
- **Markdown files open in mdit** by default, with COSMIC Edit as the fallback.
  mdit is not pinned in the dock.

### Fixed: Logitech mice and other level-only batteries showed 0% (found on behoweather, 26.10.23)

- Settings → Power listed a Logitech G305 with 0% and a "missing battery"
  icon, though the kernel reported `capacity_level=Full`. Buildroot 2026.08
  pins UPower 0.99.19 against libgudev 238, which already strips the trailing
  newline from sysfs values. UPower then chopped the last character as well
  ("Ful"), matched no level, and never updated the device. Every peripheral
  that reports a level rather than a percentage was affected (most Logitech
  HID++ mice and keyboards, many game controllers). Carried as upstream commit
  6bfe66a (in UPower 1.90.1): `patches/upower/0.99.19/`, ledger entry in
  `divergence.toml`. Verified on behoweather: battery-level full, 100%, shown
  in Settings.

### Added: mdit, the Markdown editor

- **mdit** ([patw/mdit](https://github.com/patw/mdit), MIT) ships in the
  Desktop: a Qt6 Widgets Markdown editor with a live preview and HTML/PDF
  export, themed by cutecosmic. New recipe `package/mdit` (0.1.2, from the
  release tag). Qt6 PrintSupport is now built for its PDF export.

### Fixed: logout killed ssh users' tmux, a Store catalogue that never refreshed, a theme parse error (found on behoweather, 26.10.23)

- **Ending a login session killed everything the user had left running.**
  Buildroot builds systemd with meson's `default-kill-user-processes=true`, so
  logind reported `KillUserProcesses=yes`: a tmux, screen or `nohup` job
  started over ssh died with the connection. That hit Server users the most.
  A core drop-in (`/usr/lib/systemd/logind.conf.d/50-pengyos-keep-user-processes.conf`)
  sets `no`, as Fedora, Debian and Arch ship it.
- **The COSMIC Store never refreshed a system-wide Flathub's catalogue.** Its
  hourly appstream refresh passed the display id `"flathub (system)"` as the
  remote name ("Remote … not found"), so new apps and descriptions never
  appeared until a `flatpak update`. behoweather's copy was 4 days old.
  cosmic-store now carries a patch
  (`cosmic-patches/cosmic-store/0001-…`; still unfixed upstream).
- **Settings logged "1:1: Expected identifier" on every start.**
  cosmic-settings' own `default_schema` ships `CosmicTheme.Dark.Builder/v2/palette`
  without its `Dark(…)` variant, so the dark builder palette never loaded and
  fell back to the built-in default. `theme-defaults/` now carries a corrected
  copy.

### Fixed: no CPU microcode, whole classes of hardware without drivers, COSMIC features that did nothing (found on behoweather, 26.10.22)

The 26.10.22 hardware check (`pengyos-oct11-issues.md`) and a sweep of the
families around each finding. Most of these were silent: the machine worked,
and the feature was simply not there.

- **No edition shipped CPU microcode.** behoweather's N100 ran its factory
  revision (0xe; Intel's 20260812 release has 0x21): "Running old microcode",
  "Register File Data Sampling: Vulnerable: No microcode". Intel
  (`intel-microcode`) and AMD (`amd-ucode`) microcode is now built into the
  kernel (`CONFIG_EXTRA_FIRMWARE`, set by `external.mk`), and the early loader
  applies it. It rides inside `bzImage`, so existing installs get it from a normal
  update. No grub.cfg or updater change.
- **Hardware outside the fleet had no driver.** The kernel config grew one owned
  machine at a time. Added (desktop fragment): `xe` (Lunar/Panther Lake, Arc
  B-series: no GPU acceleration before), Intel Wi-Fi 7's MLD op mode, MediaTek
  MT7925, Qualcomm ath12k and more rtw89 parts (+ firmware), the AMD/Intel PMC
  drivers (s2idle never reached deep sleep: battery drain in suspend),
  intel-hid/vbtn (power button and hotkeys on many Intel laptops), ThinkPad/
  IdeaPad/ASUS/HP/Dell/Acer platform drivers, the CS35L41/CS35L56/TAS2781 speaker
  amplifiers (silent speakers) with their tuning firmware, the Ryzen 7040+ digital
  mic (ACP "PS"), Wacom/Microsoft/Lenovo/ASUS/Apple/Elan HID and RMI4/Elan
  touchpads, sensor hubs, VirtualBox/VMware/Hyper-V graphics, Bluetooth PAN.
  Core: the Intel IOMMU on by default (AMD's already was), Yama, Landlock and
  WireGuard (`wireguard-tools` and NetworkManager's WireGuard shipped without the
  kernel module).
- **QEMU + KVM on Server and Desktop.** `qemu-system-x86_64` with KVM, TCG
  fallback, SeaBIOS/edk2 guest firmware and `qemu-img`; vhost, VFIO and macvtap in
  the kernel; an SDL/OpenGL window on the Desktop.
- **Programs COSMIC runs by name were missing.** Every `Command::new("…")` in the
  pinned COSMIC sources was checked against the image:
  - `cosmic-osk` was never built, but cosmic-session started it at every login and
    the panel's OSK applet toggled nothing. Now built (`cosmic-components.tsv`).
  - no `rfkill`: Bluetooth couldn't come back after airplane mode.
  - no `pactl`: Mono Audio didn't reach PulseAudio clients (every Flatpak browser,
    Discord, Spotify), and virtual sinks couldn't be made the default. PulseAudio
    is now built client-only (no daemon to fight `pipewire-pulse`).
  - no `qalc`: the launcher's calculator did nothing (new `libqalculate` recipe).
  - no `locale`, and only `en_US.UTF-8`: Settings → Region & Language listed no
    languages at all. ~50 UTF-8 locales now, the `locale` binary, and the X11
    Compose data for all of them.
  - no `xrdb` (X11 apps' cursor theme and size) and no `notify-send` (new
    `libnotify` recipe).
  - Not done: Orca, the screen reader the greeter's accessibility toggle starts.
    It needs GObject-introspection and a speech stack, which means rebuilding
    most of the desktop. Tracked as a known gap.
- **GameMode did nothing.** Its polkit actions are allowed only for group
  `gamemode`, which no image created (no `systemd-sysusers`). The group exists
  now and the installer adds the user. `merge-accounts` adds existing owners
  once to any group later appended to `/usr/lib/pengyos/owner-groups`, which also
  fixes `pipewire` membership for machines installed before that group existed.
  Groups `clock` (udev) and `power` (thermald's D-Bus policy) are defined, ending
  two boot-log warnings, and `udevadm verify` passes again.
- **App updates were never announced.** COSMIC Store checks for Flatpak updates
  only while it is open, and never notifies. The PengyOS Updates notifier now
  asks Flatpak after login and every 6 hours, and announces new app updates with
  a link to the Store (pengyos-updates 0.3.0).
- **`vm.max_map_count` was 65530**, which crashes Steam/Proton titles and is
  below Elasticsearch's minimum. Now 1048576 (every edition). The Desktop also
  allows 8192 inotify instances (the other half of 26.10.20's watcher fix).
- **Two mDNS stacks.** 26.10.20's `MulticastDNS=resolve` stopped the hostname
  conflict, but resolved still bound :5353 next to avahi ("Detected another
  IPv4 mDNS stack"), and nsswitch asked resolved first, so `mdns4_minimal` never
  ran. resolved's mDNS is off, and `hosts:` asks avahi for `.local` first
  (Fedora's order).
- **Config nothing read.** `tools/pengyos-desktop-net` emitted
  NetworkManager keys that don't exist (`auth-agent=`, `[device] devices=*`), a
  `wpa_supplicant.conf` with a placeholder network, COSMIC panel/settings TOML
  that COSMIC never reads, and `/wifi-firmware.txt` at the filesystem root. The
  tool is retired, and only a valid `NetworkManager.conf` remains.
- Smaller: `xfs_scrub_all.timer` disabled (no PengyOS partition is XFS),
  `/etc/bluetooth` gets the 0555 its unit declares (a warning at every boot), and
  `/etc/pam.d/login` no longer names `pam_selinux`/`pam_console`, which the image
  doesn't have (an error-level PAM log line at every console login).

### Fixed: an inert amdgpu power rule and two boot-log warnings (found on behoweather, 26.10.20)

- **The amdgpu runtime-PM udev rule never did anything.** udev rejected it at
  every boot (`60-pengyos-amdgpu-runtime-pm.rules:15 Invalid key/value pair`)
  because a value was unquoted. Two other mistakes would have kept it inert
  after that: it set `device/runtime_power/*`, which doesn't exist (the
  attributes are `device/power/{autosuspend_delay_ms,control}`), and it matched
  `DRIVER=="amdgpu"` on the DRM card node, which has no driver. It now matches
  the PCI parent with `DRIVERS==`, and `udevadm verify` passes.
- **`pengyos-server.target` named itself in `Before=`.** systemd dropped the
  dependency and warned three times per boot. The line is gone.
- **`cups-browsed.service` was installed 0755.** systemd warned that it "is
  marked executable" three times per boot. The Desktop post-build now sets it
  to 0644.

### Fixed: two NTP clients, mDNS name conflict, low inotify limit, theme load errors (found on behoweather, 26.10.19)

- **chrony and systemd-timesyncd both ran, on every edition.** Buildroot builds
  timesyncd by default, and nothing turned it off next to the core layer's chrony.
  chronyd logged `System clock interference detected` every few minutes. The core
  layer now drops timesyncd (SPEC already chose chrony). A `ntp-units.d` entry
  points systemd-timedated at `chrony.service`, so `timedatectl set-ntp` and the
  automatic-time switch in COSMIC Settings still work.
- **avahi renamed the Desktop `<host>-2`…`-4` on every boot.** No other device
  was using the name. systemd is built with `default-mdns=yes`, so resolved also
  published the hostname on :5353, and avahi conflicted with it. The Desktop now
  ships `MulticastDNS=resolve`: resolved still looks up `.local` names
  (nsswitch asks it first), but avahi is the only thing that advertises the
  hostname.
- **VS Code opened with "Unable to watch for file changes".** The kernel sizes
  `fs.inotify.max_user_watches` from RAM (137830 on 14 GB). The Desktop now sets
  524288 in `/usr/lib/sysctl.d/60-pengyos-inotify.conf`.
- **xdg-desktop-portal-cosmic logged `error loading system light theme`.** This
  was not a missing variant. The shipped `CosmicTheme.{Light,Dark}/v2` had no
  `list_button` or `frosted_maximized_apps`, keys the pinned libcosmic reads (the
  cosmic-settings default schema doesn't have them either). Without them,
  `list_button` fell back to stock colours instead of the PengyOS accent. Both
  keys are now in `theme-defaults/`. `list_button` was computed with the pinned
  libcosmic from the same builder input, which reproduces the shipped
  `link_button` and `accent_button` byte for byte.

The NTP, mDNS and sysctl changes were checked on behoweather with `/etc` drop-ins
(since removed): avahi kept `behoweather.local` with no conflict and `.local`
lookups still resolved. `set-ntp false/true` stopped and started chrony, which
synchronised. The watch limit read 524288.

### Fixed: three silent fallbacks on the Desktop (found on behoweather, 26.10.18)

- **`fwupd-refresh.service` failed every time its timer fired** (`217/USER`): the
  unit runs as `fwupd-refresh`, which upstream creates through `sysusers.d`, and the
  image has no `systemd-sysusers`. The user is now in `board/desktop/users.table`.
- **flatpak-portal rejected `pengyos-background.portal` on every boot** (`Key file
  does not have key "UseIn"`). xdg-desktop-portal routes Background through
  `portals.conf` and kept working; the descriptor now says `UseIn=COSMIC`, like
  `oo7-portal.portal`.
- **PipeWire could not read `.flatpak-info`** (`parsing failed: Operation not
  supported`). Buildroot builds it with `-Dflatpak=disabled`, which compiles the
  parser out. Sandboxed clients were still restricted, but had no app id, and
  apps granted all devices never got the Manager role. `external.mk` now enables
  it when flatpak is in the image.

Each fix was checked on behoweather against the stock 26.10.18 files, with bind mounts:
`fwupdmgr refresh` succeeded as the user, flatpak-portal loaded the descriptor without
the error, and a Flatpak pulse client gained `pipewire.access.portal.app_id`.

### Fixed: Firefox, Chrome and VS Code had a close button only (found on behoweather, 26.10.17)

The three are Flatpaks that draw their own titlebar, and a sandboxed GTK app reads
which buttons to draw from `org.gnome.desktop.wm.preferences button-layout`, through
the **Settings portal**. `cosmic-settings-daemon` writes that key
(`:minimize,maximize,close`) and it was set on the host, but no portal served it:
COSMIC's backend answers only `org.freedesktop.appearance`, and we built
`xdg-desktop-portal-gtk` with `-Dsettings=disabled`. GTK then falls back to its
built-in `menu:close`, the close-only frame. Pop!_OS, and Ubuntu through
`xdg-desktop-portal-gnome`, ship a backend that serves the `org.gnome.*` keys.

`xdg-desktop-portal-gtk` is now built with Settings enabled, as Pop!_OS ships it
(it selects fontconfig and gsettings-desktop-schemas, both already in the image).
The 26.10.18 workaround is reverted: the `force_ssd_app_ids` cosmic-comp patch, its
shipped allowlist, its test, and the `cosmic-comp-force-ssd-app-ids` divergence. It
fixed Firefox only, by drawing a second, COSMIC titlebar above Firefox's own, and it
could not reach Chrome or VS Code. The Super+D → Minimize binding it added goes with it.

**Proven on behoweather** with the rebuilt portal bind-mounted over the 26.10.18 one:
the portal's `Read org.gnome.desktop.wm.preferences button-layout` returns
`:minimize,maximize,close`, and Firefox, Chrome and VS Code each draw minimize,
maximize and close in their own titlebar (Firefox's tab list stays to their left),
with a single titlebar each.

### Fixed: the COSMIC tray had no app icons (found on behoweather, 26.10.14)

Discord was running, SyncThingy was installed, and the panel's status area showed
nothing for either — no badge, no click-through menu. The panel said why:

```
com.system76.CosmicAppletStatusArea: Failed to start status notifier watcher:
    org.freedesktop.systemd1.NoSuchUnit: Unit com.system76.CosmicStatusNotifierWatcher.service not found.
com.system76.CosmicAppletStatusArea: Failed to register status notifier host:
    org.freedesktop.DBus.Error.ServiceUnknown: The name org.kde.StatusNotifierWatcher was not provided by any .service files
```

The tray is a StatusNotifierItem **host**: apps (Discord, Steam, SyncThingy — both
flatpaks carry `org.kde.StatusNotifierWatcher=talk`) register an item with a
watcher on the session bus, and `cosmic-applet-status-area` paints the ones it
finds. Upstream's `_install_status_notifier_watcher` installs **two** files: the
dbus activation file under `/usr/share/dbus-1/services/`, and the systemd **user
unit** that file names in its `SystemdService=` key, under
`/usr/lib/systemd/user/`. The post-build installed only the first.

That half is not enough here, and the reason is the bus: the session bus is
`dbus-daemon --session --address=systemd: --systemd-activation`, so activation is
answered by **systemd --user**, which resolves `SystemdService=` as a *unit* in
its unit search paths. A file under `/usr/share/dbus-1/services` is a pointer to
a unit, not a unit. With no unit, activation fails, no watcher process ever
starts, no host registers, and every tray icon is silently absent — while the
image looks complete and the build passes.

`post-build.sh` now installs both, from upstream's own `.service.in` templates
(the unit is `Type=dbus`/`BusName=…`, static: dbus starts it on demand).

**Why the 2026-09-30 fix didn't catch this:** that fix answered the journal
message of the time — "was not provided by any .service files" — by installing
the `.service` file. The symptom moved from "no watcher name on the bus" to
"unit not found", and nothing checked the pair. So the class is now checked, not
just this instance: `buildroot-external/board/common/check-dbus-units.py` runs at
the end of the Desktop post-build and fails the build if **any** dbus-activated
service (session *or* system bus) names a unit the image doesn't ship. Run
against behoweather's live tree it reports `36 dbus-activated service(s)
checked` and exactly one finding — this one. 10 tests in
`scripts/tests/test_dbus_activated_units.py` drive it with a fixture tree,
including the positive control (a complete pair passes and *says* what it
checked) and the negative control (the shipped-pointer-only case fails and names
the unit).

**Verified live on behoweather** (Tier 1, before any rebuild): the unit dropped
into `~/.config/systemd/user/`, `systemctl --user start` brought the watcher up
(`Acquired bus name: org.kde.StatusNotifierWatcher`), the panel registered as a
host (`IsStatusNotifierHostRegistered = true`), SyncThingy appeared in the panel
tray within seconds of starting. Discord needs one restart to register — it had
already tried, and failed, before any host existed.

**Why nothing caught it the first time:** the 2026-09-30 "no tray icons" fix
answered the journal message it had — "was not provided by any .service files" —
by installing the `.service` file. The symptom moved from "no watcher name on the
bus" to "unit not found", and nothing checked the pair. A half-fix that only
changes the error text is not a fix.

**What the new check found on its first real run — and what it got wrong.** Run
against a built Desktop tree it reported 36 dbus-activated services and 7
findings, six of which were false positives: `dbus-org.bluez.service`,
`…Avahi`, `…ModemManager1`, `…nm_dispatcher`, `…resolve1`, `…timesync1` have no
unit file in `target/` because Buildroot's `SYSTEMD_PRESET_ALL` hook runs while
assembling the rootfs *image*, and systemd creates the `dbus-<BusName>.service`
alias there, not in `target/`. Confirmed on behoweather: all 8 such aliases point
at a unit carrying the matching `BusName` (bluetooth=org.bluez,
avahi-daemon=org.freedesktop.Avahi, …, and systemd spells `_` as `-`). The check
now resolves an alias against the `BusName=` of every unit the image ships, with
a test for each direction — an alias with no unit behind it still fails.

**And it caught a second, different defect in itself:** the commit that added it
rewrote `board/desktop/post-build.sh` and dropped its `100755`, so the build host
died at `target-finalize` with `Permission denied` — the third time that file has
lost its +x. `scripts/pengyos_gate.py` now checks the git index mode of every
script Buildroot declares in `BR2_ROOTFS_POST_BUILD_SCRIPT` /
`BR2_ROOTFS_POST_IMAGE_SCRIPT` (sourced files like `image-ids.sh` and
`/etc/profile.d/*` are deliberately not in the set — 0644 is right for them).

### Fixed: the live desktop image stopped at a login screen (found on 26.10.15)

Found on behotv booting the 26.10.15 validation image: `pengyos-desktop-x86_64.img`
came up at the COSMIC greeter and asked for a password, instead of logging in and
showing the desktop. It is not a driver issue and it is not new — it has been true of
every plain live desktop image.

`greetd-config` decides whether to add an automatic session, and it decided from the
**`PENGYOS-PAYLOAD` partition label** — a partition only the *installer* stick has.
That label answers "the installer is available", not "this boot is live", and the two
are different facts. The Server and Core console gettys already ask the right question
(`agetty-autologin`: an installed system boots with `pengyos.data=` on the kernel line,
a live image does not); the Desktop was the one place using the wrong proxy.

Now the Desktop uses the same test. A live boot logs root in; an installed system shows
the greeter, and `pengyos-autologin on USER` remains the opt-in. 8 tests drive both
branches, including the negative controls (installed never autologs root, a
non-existent account is refused), and they fail if the payload label comes back.

**Why nothing caught it:** `scripts/release.sh` session-checks only the *installer*
image, because `pengyos-qemu-verify desktop` requires `pengyos-install` — a process the
plain image has no payload to run. So the image a person actually boots was checked by a
line whose only assertions are "reached a login prompt" and "no kernel panic". A login
prompt passes that. `pengyos-qemu-verify` 0.6.0 adds `desktop --no-installer`, and
`release.sh` now session-checks **both** desktop images. Reproduced before the fix and
verified after: the plain image failed with `cosmic-panel-running FAIL` (greeter on
screen), and now passes 4/4 reaching the COSMIC session.

### Fixed: the same live image had no audio (found on 26.10.15)

Found on the same behotv boot as the login-screen defect above: COSMIC's Sound
settings listed **no device profiles** and the box played nothing over HDMI (the TV,
which works on that machine under Ubuntu). The four `10-pengyos-live-root.conf`
drop-ins (pipewire / pipewire-pulse, service and socket) let root's PipeWire run on a
live boot, and they gated that on the **`PENGYOS-PAYLOAD` partition label** — the same
wrong proxy, failing the same way. The plain live image has no payload, so
`ConditionPathExists` never held, PipeWire and WirePlumber were skipped
(`no trigger condition checks were met`), and root's session had no sound server at
all — hence no sinks and no profiles to show. Starting PipeWire by hand on the box
brought the NVIDIA HDMI sink straight up (`GA104 … Digital Stereo (HDMI)`,
`api.alsa.use-acp=true`) and a tone reached `card0/pcm3p`; the driver, codec, ELD and
ACP profile were all fine. The drop-ins now use
`ConditionKernelCommandLine=|!pengyos.data`, the same kernel-line test `greetd-config`
and `agetty-autologin` use (the Server update units already spell it
`ConditionKernelCommandLine=pengyos.data`). Note `ConditionKernelCommandLine=` matches
a whole word or a `word=value` pair, which is why the argument is `pengyos.data`, not
`pengyos.data=`. Positive/negative control on the box: with the kernel-line condition
`systemctl --user start pipewire` goes **active**; with the payload condition it is
**skipped**. `scripts/tests/test_pipewire_live_root.py` guards the shape and the
matching rule.

### NVIDIA driver: 595.104.02 -> 610.57.04 (merged; proven on behotv)

`nvidia-open` moves from the 595 production branch to NVIDIA's 610 feature branch.
595.104.02 was already the newest release on the production branch, so the only way to
move was onto a feature branch - which is an operator's call, and is why this went
through a branch, a QEMU validation build, and a real-hardware boot rather than the
nightly loop (`nvidia-open` is report-only to it: `guards.DENIED`).

Checked against the 610.57.04 `.run` (463,025,450 bytes, sha256
`b2e935c66b83bb00c0c857bc8e0ee0fd52de9286b40c9cc1eec29a7ce7eb116d`), extracted and
compared with what the recipe installs: every versioned library `NVIDIA_OPEN_LIBS` names
is present, and the side-versions the recipe hardcodes instead of deriving -
`libnvidia-egl-gbm.so.1.1.3`, `libnvidia-egl-wayland.so.1.1.20`,
`libnvidia-egl-wayland2.so.1.0.1`, `libnvidia-egl-xcb.so.1.0.5` and `...-xlib.so.1.0.5`,
`libnvidia-nvvm70.so.4` - are unchanged from 595. `LICENSE` is byte-identical. So the
bump is the version and the hash, not a rewrite of the install list.

Proven on behotv (RTX 3070) 2026-10-08, booting the 26.10.15 validation build: NVRM
610.57.04 built by our gcc 15.3.0 against kernel 7.2.5; `nvidia`, `nvidia-modeset`,
`nvidia-drm` and `nvidia-uvm` all loaded; GSP firmware present for 610 only;
`nvidia-drm` is the display (card1, DP-1/2/3 + HDMI-A-1/2); and `nvidia-smi` lists
COSMIC processes on the GPU (`cosmic-app-library`, `cosmic-files-applet`,
`xdg-desktop-portal-cosmic`, `cosmic-term`). No failed units.


### Docs: the README became the front page (2026-10-07)

`README.md` was rewritten for the person deciding whether to try the OS: what each
edition actually is, how to install it, what's different here, and what isn't ready
yet. The design record it carried moved, intact, to **`docs/principles.md`** (new):
the ten principles, the component selection rubric, the
OBSERVE → PROPOSE → BUILD → VERIFY → RATIFY loop, the failure-modes table and the
stack-down summary. The build / QEMU / flash recipe went into `CONTRIBUTING.md`
("Setting up a development machine"), where the build half already lived. Repointed
to the new homes: `SPEC.md` §5 (the rubric link), `docs/stack-down-replace.md` (the
failure-modes citation), and the doc maps in `CONTRIBUTING.md`, `docs/README.md` and
`CLAUDE.md`. One stale claim went with it: the README said the NVIDIA editions had
never been booted on an NVIDIA machine. **Desktop NVIDIA has** — first metal boot at
26.10.7, an RTX 3060 Ti with two 1440p monitors, the fix being the Desktop board's
`60-pengyos-amdgpu.conf` stubbing `nvidia*` to `/bin/true` (see "Fixed (26.10.7)").
Server NVIDIA is still QEMU-only.

### Fixed — the download page offered testing images
`scripts/release.sh --publish` runs `download-publish` for every release, testing
included, and the download page listed whatever version was newest, so a testing
build became the image new users installed before anyone had confirmed it on real
hardware. It also listed only that newest version's images: 26.10.13 was published
for the two Desktops alone, which took Server, Server NVIDIA and Core off the page.
The page now lists, per edition, the newest version promoted to **stable**, and
`update-promote` regenerates it (`pengyos-release` 0.10.0). Testing images are still
compressed and signed at publish time, from the build that was boot-checked, and
wait unlisted in `download/<version>/` until promotion.

Editions can now show different builds, so each card leads with the release line
(`26.10`) and gives the build as small print. Policy (CONTRIBUTING.md,
"Versioning"): a patch build ships only the editions it changes, and each month's
first release ships all of them, so the editions always share a release line.

### Fixed (26.10.13) — COSMIC Files drew a second copy of its view on the desktop
`cosmic-files` (the file manager) and `cosmic-files-applet` (the desktop) are one
cargo workspace, and the applet depends on the app crate with the `desktop-applet`
feature. The host build built the component with `cargo build --release
--workspace`, so cargo **feature-unified** `desktop-applet` onto the `cosmic-files`
*app* binary too. The app therefore also ran the applet's output-event handler and
created a full-screen `Layer::Bottom`, all-edges-anchored layer surface showing
`~/Desktop`: opening Files drew a second, interactive copy of the file view on the
desktop wallpaper (over the real window, behind everything), and it followed the app
across workspaces. Reported from behoweather (26.10.11); reproduced in QEMU on
26.10.12 (virtio-gpu-gl + SDL/GL under Xvfb, QMP-injected input) and root-caused by
building the app and applet together vs. separately. The component is now built as
**two cargo invocations** — `-p cosmic-files` then `-p cosmic-files-applet`
(`scripts/cosmic-components.tsv`) — and `scripts/build-cosmic.sh` fails the build if
the app binary carries the applet code; `scripts/tests/test_cosmic_files_build.py`
guards the TSV in the gate.

### Fixed (26.10.11) — pengyos-alsa-unity.service was dead in 26.10.10 (script shipped non-executable)
26.10.10 shipped `/usr/libexec/pengyos/alsa-unity` as `0644`: an edit dropped the
`+x` that `afcefef8` had set, and the rootfs overlay is copied with `rsync -a`, so
the mode went straight into the image. `pengyos-alsa-unity.service` then failed at
boot with `203/EXEC` ("Unable to locate executable ... Permission denied"). Audio
was unaffected on the N100 (stock ACP makes the service redundant), but the laptop
unity-gain fix it exists for was not being applied. The mode is restored, and the
gate now fails if a unit `ExecStart` resolves to a non-executable overlay file.

### Fixed (26.10.10) — HDMI audio: drop the split-mode WirePlumber rule, inherit stock ACP
`51-alsa-ucm.conf` set `api.alsa.use-acp = false` for PCI cards, so every ALSA
PCM became a sink of its own. On the N100 (one HDA card: ALC897 + Intel HDMI)
that split-mode HDMI sink — `api.alsa.path=hw:0,3`, 64 channels, `generic-mix` —
was **silent**, while `aplay -D hw:0,3` on the same PCM was audible, and a full
state diff between the working (ACP) and broken (split) configs was 5 lines, all
PipeWire node properties. Removed the override; WirePlumber now uses stock ACP,
as Ubuntu/Fedora/Pop!_OS ship. On a single-codec card ACP defaults to analog
(priority 6565 > 5900, and the ALC897 line-out has no jack-detect), so HDMI is a
**selectable profile**: pick `Digital Stereo (HDMI)` in Settings → Sound →
Device profiles. `alsa-ucm-conf` stays (ACP reads it); `pengyos-alsa-unity.service`
stays pending a laptop re-measure. Investigation: `docs/hdmi-audio-problems.md`.

### Desktop: 20 new Pengy wallpapers, and Cosmic Pengy redrawn (2026-10-06)
Twenty backgrounds — four seasons, seven North American holidays, nine Pengy
adventures — at 2720×1530 exact 16:9, each anchored on COSMIC's own accent
swatches read out of libcosmic `cosmic-theme/src/model/light.ron`. JPEG q92
4:4:4: 18 MB for the set rather than 93 MB as PNG. Chroma subsampling is the
decision that matters for this line art — 4:2:0 visibly softens thin bright
outlines over dark fills, so 4:4:4 it is.

`cosmic-pengy-16x9.png` is replaced by a redrawn `cosmic-pengy-16x9.jpg` in the
same style; the original was 1344×768, i.e. 7:4, not 16:9 despite its name. The
old name survives as a **symlink** to the new file: existing installs carry a
user-level `CosmicBackground` record pinning the old absolute path (behoweather
does; behobasement does not), and deleting it would leave those machines with a
blank desktop after an upgrade rather than a build failure. Buildroot applies
overlays with `rsync -a`, so the link reaches the target intact.

Adding a wallpaper needs **no config edit**: cosmic-settings populates the picker
by walking `$XDG_DATA_DIRS/backgrounds` (`WalkDir`, `max_depth 3`), and
`CosmicBackground/v1/all` is a single `Entry` naming only the *active* wallpaper.
That record is, however, an absolute path — so reformatting the active wallpaper
without editing it yields a blank desktop rather than a build failure. `pengyos-gate`
grew `backgrounds:` and `backgrounds-aspect:` checks for exactly that, both
verified against negative controls. Provenance and a redraw script live in
[`art/wallpapers/`](art/wallpapers/).

### pengyos-nightly 0.5.0: a batch of changes, one release (2026-10-06)
The act lane's cap was never a hardware limit, and the numbers say so: one complete
action — guards → branch → patches → recompose → gate → dirclean → build → QEMU boot
checks → sign → publish → push → merge — measured **18 m 14 s** on 2026-10-05, and all
five editions rebuilt incrementally in **9 m 42 s** on a 32-core host. `max_actions = 1`
was capping *CVEs closed* (60 P1 open, one per night) while paying the full verification
cost once per action.

Phase 3 separates the two things the old cap conflated:

- **`max_actions` 1 → 12** — how many changes the loop may prepare and verify in a night.
- **`max_releases` = 1** (new) — how many signed images may reach the feed in a night.
  The blast radius per night is unchanged: twelve changes in one image is still one
  image, still testing-only, still no code path to `update-promote`.

One batch now means one branch, one commit per change, one gate, one build, one boot
check, one signature, one published version for the **union** of the editions touched,
and one notes file naming every change with its evidence. The boot test of the batch is
the sign-off; the operator's real-hardware check happens the next day, which is what the
`testing` channel is for.

Two safety properties found while building it, both now tested:

- **Pre-flight decides what is real, before anything is committed.** A package whose
  `<pkg>-dirclean`/`<pkg>-patch` fails is dropped from the batch, its edits are reverted
  out of the tree, and it is recorded as `patch_does_not_apply` so the planner refuses it
  by name next night. Before this change the edits were committed *first*, so a dropped
  change could be published and merged into `main` without ever having been built.
- **New guards.** `release_cap` — a second `act --apply` the same night publishes
  nothing. `batch_conflict` — two changes that want the same file are refused before a
  branch exists, because sequencing them is a human's decision.

`status` now reports a blocked batch as a problem rather than a quiet night, and prints
the batch policy (`up to N change(s) -> M release(s)/night`). 144 tests green
(14 new in `tests/test_batch.py`); repo gate `--mode full` 5/5. Machine policy updated
on behoserver: `max_actions = 12`, `max_releases = 1`.

### Decisions recorded: the replacement ladder, the divergence budget, our AI-contribution stance (2026-10-06)
The project's long-term direction existed only as agent memory. It is now durable
(Principle 10), and the two controls it needs exist as numbers rather than stances.

- **`docs/stack-down-replace.md`** (new) — the ladder, banded by **verifiability,
  not difficulty**: additive → fork upstream → replace a userland component →
  replace a contract-bearing subsystem (research track) → not on the list. Kernel,
  NVIDIA blobs, Wayland protocol and a browser stay non-goals, and the reason is
  recorded as *what we can check*, not *what an agent can write*. Five-part
  admissibility test for any replacement: Lane-1 recipe, provable by boot + smoke
  matrix, **its own observe source**, ledgered, repro not silently voided. Records
  the Buildroot mechanism that makes replacement legitimate from a `BR2_EXTERNAL`
  tree (virtual packages: `provides/*.in` + `BR2_PACKAGE_PROVIDES_*` +
  `<PKG>_PROVIDES`; the upstream manual's own example replaces the **init** system),
  and states the narrower truth about the 2026-10-05 finding — a stock package
  cannot be *shadowed* or *version-bumped* from here, but it **can** be replaced.
  COSMIC is recorded as the lesson already paid for: a replacement performed as
  host-built injection, which cost the Desktop its reproducibility claim.
- **`divergence.toml`** (new) — the divergence ledger, machine-readable, one entry
  per carried divergence with a mandatory **expiry condition** and **observe
  source**. Budget set: **at most 10 maintained forks** (current: 0); creating a
  fork is **Tier-2**, a human sign-off, not a nightly action. First entry:
  `libxml2-2.15.3-cve-2026` — 7 CVEs, 7 named upstream commits, `natural_fix =
  2.15.4`, expiring when a Buildroot point release pins libxml2 ≥ 2.15.4.
- **`docs/ai-contribution-stance.md`** (new) + an **Attribution** section in
  `CONTRIBUTING.md` — disclose with the host project's own convention (kernel-style
  `Assisted-by:`), a person signs every outward submission, agent accounts do not
  open PRs into other people's projects, and an upstream "no" is respected by
  carrying the divergence openly instead of routing around it. Grounded in the
  2026 record: the kernel's codified policy (April 2026), Godot's ban on
  AI-authored code (June 2026) and its *demoralization* argument, which we answer
  with contribution format rather than argument.
- **`buildroot-external/patches/README.md`** (new) — makes the `patches/` promise in
  Principle 6 actually true: upstream commits only, a ledger entry per divergence,
  and a `<pkg>_IGNORE_CVES` declaration so `pkg-stats` can see a carried patch at all
  (it cannot today: `set_patch_count()` walks only the package's own directory, so
  patches reached via `BR2_GLOBAL_PATCH_DIR` leave a package reporting "affected by
  CVEs" forever).
- **`SPEC.md` §6** — three decisions closed: #9 the ladder, #10 the divergence
  budget and fork policy, #11 the outward contribution posture.

No build, image or config changed. The next step this unlocks is enforcement: a
`pengyos-recipe-lint` rule that every `IGNORE_CVES` entry names a patch file that
exists and matches a ledger row, and a gate check on the fork budget.

### Published: 26.10.8 to **testing** (2026-10-05, by `pengyos-nightly act`)
The first release the loop published itself, and the first time the observe →
decide → build → publish half of the system ran end to end without a human in the
middle. `security-libxml2-2.15.3`: 7 upstream fix commits (NVD named one per CVE)
carried onto the pinned 2.15.3 as `buildroot-external/patches/libxml2/2.15.3/*.patch`,
built for Desktop and Desktop NVIDIA, boot-checked under QEMU, signed, published to
**testing** and pushed to the live feed. **Stable is untouched** — 26.10.7 remains
what stable machines get, and `pengyos-nightly` has no code path to `update-promote`.

Proof, checked independently of the tool's own claim:
- `build/libxml2-2.15.3/dict.c` carries the patch's `INT_MAX / 2` overflow checks, no
  `.rej` files anywhere, and `target/usr/lib/libxml2.so.16.1.3` was rebuilt at 18:03 —
  the fix is in the image, not just in the tree.
- Both Desktop installer sticks were rebuilt and re-checked in the same run
  (`qemu-desktop-check` at 18:07 and 18:14, `systemctl --failed` empty).
- behoweather, which is on `channel = testing`, answers *"PengyOS 26.10.8 is available
  (testing); you have 26.10.7"*. That is the acceptance test that matters.
- `https://pengyos.catbee.ca/update/feed.xml` lists 26.10.8 for both Desktop editions
  with `channel = testing` only.

### Build-up: 26.10.8 (2026-10-05, built and gated, then published to testing)
`VERSION` is 26.10.8 and all five images are built and gated. What is in it so far:
- **dnsmasq on both Desktop editions** (the Wi-Fi hotspot fix, in *Added* below).
- **PengyR 1.10.0 → 1.11.2.** `package/pengy` was two releases behind upstream;
  behoweather confirmed it at runtime (`pengy-cli --version` → `Pengy v1.10.0` on
  a 26.10.7 Desktop). `pengy.hash` is regenerated rather than carried over,
  because the hash covers the cargo-*vendored* tarball Buildroot produces at
  download time. Verified inside a booted 26.10.8 Server guest: `pengy-cli
  --version` and `pengy-web --version` both report **Pengy v1.11.2**,
  `IMAGE_VERSION="26.10.8"`, no failed units. 1.11.2 landed at 13:38, after the
  12:15 build that carried 1.11.1 — the pin went stale during the day, which is
  what `pengyos-nightly` 0.2.0's new `pengyr` observe source is for.
- **Gates re-run on the 26.10.8 images:** Core smoke matrix 44 checks / 0 failed,
  Server smoke matrix 50 / 0 failed, and both Desktop installer sticks pass the
  COSMIC session check (5 checks, 0 failed each). Reports in
  `~/build/pengyos/verify-26.10.8/`.
- **COSMIC moves to epoch-1.9, with one libcosmic.** 16 of the 26 tagged component
  pins carry new code (greeter to 1.9.1); the rest already pointed at the commit
  epoch-1.9.0 tags. The part that actually matters is `scripts/cosmic-libcosmic.pin`
  + `scripts/cosmic-libcosmic-pin.py`: the image used to build COSMIC against **14
  distinct libcosmic commits** because each component's own `Cargo.lock` had frozen
  its own, and it now builds against **1**. `build-cosmic.sh` applies the pin per
  component and then verifies the *lock*, not the manifest — a cargo patch can be
  accepted and silently unused, which is exactly what happened to cosmic-idle (it
  wanted `iced_core 0.14.0-dev` from 2025-10-13 and kept its year-old libcosmic
  until the lock was re-resolved). Survey and reasoning: `docs/cosmic-epoch-1.9.md`.
- **The repo gate now tests `scripts/`.** `check_toolkit_suite` walks `tools/`, so
  build-cosmic.sh, compose-defconfigs.py and release.sh — which decide what an
  image contains — had no test coverage at all. `scripts/tests` (15 tests for the
  pin helper) is now a gate check; mutation-checked by making one fail.

### Verified (26.10.8 with COSMIC 1.9)
26 cargo builds, all coherent on `87ab8179e1bd`. Both Desktop editions rebuilt;
`pengyos-qemu-verify desktop` passes on both installer sticks (5 checks, 0 failed
each). The 26.10.8 session capture and the 26.10.7 one differ in **131 pixels out
of 1,024,000**, all inside a 28x10 box — the panel clock. Not done: the
real-hardware round, which is where the greeter/wallpaper failure mode this move
targets actually lives.
- **`pengyos-nightly` 0.2.0 observes the PengyR pin.** The agent every image ships
  is an authored recipe with its own upstream, so `pkg-stats` cannot see it go
  stale — the loop that exists to catch stale pins was blind to the assistant
  itself. A patch bump is routine, a new minor is a major finding.

### Added (2026-10-05)
- **`pengyos-nightly` 0.4.0 — the act lane (phase 2).** The loop can now act on what
  it observes: `plan` decides deterministically (NVD's own affected ranges, pkg-stats'
  recipe paths and CPE ids) and records every candidate it refused *with the guard that
  refused it*; `act` runs one allowed action end to end into **testing** — branch, edit
  the pin, recompose defconfigs, gate, `<pkg>-dirclean` + `<pkg>-patch`, build,
  boot-check, sign, publish, merge to `main`, record. Stable is unreachable in code:
  the publish channel is a constant and every argv is re-checked before it runs, so
  `update-promote` and `--channel stable` cannot be assembled. Cron: 19:00 observe +
  triage + report + plan, 21:00 act. Kill switch: `act_enabled` in
  `~/build/pengyos/nightly/nightly.toml`. See `tools/pengyos-nightly/spec.md`.
- **A stock Buildroot package cannot be version-bumped from a br2-external tree** —
  established by a build, not by reading docs. `LIBXML2_VERSION = 2.15.4` in
  `external.mk` made Buildroot download 2.15.4 and check a hash supplied through
  `BR2_GLOBAL_PATCH_DIR`, but the pattern rule `$(BUILD_DIR)/%/.stamp_extracted`
  (`package/pkg-generic.mk:206`) had already fixed the stamp path and `$(@D)` when the
  stock recipe was parsed (`include package/Makefile.in` is Makefile line 526,
  `include $(BR2_EXTERNAL_FILE)` is 559), so extraction died on a missing
  `build/libxml2-2.15.3` → `2.15.4` mismatch. `dirclean` did not help. The supported
  shape — now what the security lane does — is carrying upstream's fix commits as
  patches in `$(BR2_GLOBAL_PATCH_DIR)/<pkg>/<version>/`. Two consequences worth
  knowing: a new patch is invisible to an already-built tree (the stamps say "done",
  so `<pkg>-dirclean` is mandatory), and a cherry-picked commit can apply to the source
  while failing on upstream's own test files (podman's did), which is why `<pkg>-patch`
  runs as a pre-flight before any build.
- **`BR2_GLOBAL_PATCH_DIR` is now set in the core layer**
  (`$(BR2_EXTERNAL_PENGYOS_PATH)/patches`), so the external tree has a supported place
  to carry upstream fixes for stock packages. Defconfigs are regenerated from the
  layers, as always.
- **The Desktop Wi-Fi hotspot can now come up.** NetworkManager's shared mode
  starts `/usr/sbin/dnsmasq` for the lease pool; `external.mk` already named that
  path so the build stopped borrowing the host's copy, but the image never carried
  it, so "share this connection" could not start. `BR2_PACKAGE_DNSMASQ=y` is now
  in the desktop layer, and both Desktop editions build it (459 KB in
  `/usr/sbin`). Buildroot only installs a dnsmasq init script under sysvinit, so
  on a systemd image this adds the binary and nothing that starts by itself.
  Testing a real hotspot on a laptop is still open.
- **`pengyos-qemu-verify --ssh`** (tool 0.5.0): in-guest checks over the channel
  the images already provide, for the facts a serial log cannot show. Verified on
  a real 26.10.7 Server image under KVM - failed-unit and sshd checks pass, and
  `pengyos-update status` correctly fails on a raw disk image that has no A/B
  layout yet.
- **Publishing audit** (working notes, kept out of the public tree): what is clean (no
  secrets, no large blobs, LICENSE/SECURITY/CONTRIBUTING in place), what is not
  (97 home-network machine-name mentions, one personal email in a
  `pyproject.toml`, 310 commits authored as the personal address, no remote
  configured), and the order the scrub has to happen in - a history rewrite is
  only cheap before the first push.

### Verification (2026-10-05)
- **The release gate got stricter, and 26.10.7's skipped checks were paid.** That
  release went out without its QEMU boot checks (`WARNING: no writable /dev/kvm` —
  SVM was disabled in BIOS that day). With KVM back, all five images pass the
  checks `release.sh` would have run. Two gaps that re-run exposed are closed in
  `pengyos-qemu-verify`: the Core and Server matrices now prove the autologin
  shell works (a login *prompt* is not a login — 26.10.3 looped `Permission
  denied` while every check the matrix had passed), and `network-target` survives
  a console line that a kernel message split. The Desktop session check moved from
  `scripts/qemu-desktop-check.py` into the tool as `pengyos-qemu-verify desktop`,
  so the Desktop gate is one tool with one exit code and one report.

### Fixed (26.10.7)
- **Desktop NVIDIA never loaded the NVIDIA driver** (first metal boot, an
  RTX 3060 Ti with two 1440p monitors). The Desktop board's
  `60-pengyos-amdgpu.conf`, written when NVIDIA was out of scope, routed
  `nvidia`, `nvidia_drm`, `nvidia_modeset` and `nvidia_uvm` to `/bin/true`, and
  Desktop NVIDIA reuses that board: COSMIC ran on simpledrm (one monitor at the
  firmware mode, software rendering). With the lines removed, `nvidia-drm`
  binds by modalias, `nvidia-smi` works, and both monitors light up.
  `pengyos-desktop-nvidia lint` gains a `driver-not-stubbed` rule so a board
  can't do this again.
- **A broken Intel ME held up the kernel by ~6 s** (an ASUS PRIME Z790-P).
  `mei_me` was built in (the x86_64 default), so its `hw_start` retries ran
  during kernel init, behind a black screen, before the first framebuffer.
  `CONFIG_INTEL_MEI` and `CONFIG_INTEL_MEI_ME` are now modules (core kernel
  fragment): udev loads them in the background.
- **The serial console slowed boot by ~5 s on real hardware with a COM port**
  (the same board has a COM header, so GRUB's UART check adds
  `console=ttyS0,115200`, and every kernel message took ~6 ms to go out).
  GRUB now adds `loglevel=4` with it, except under QEMU (SMBIOS vendor), which
  keeps the full kernel log for testing; systemd's status lines are unaffected.
  Board `grub-efi.cfg`s and the installed `grub.cfg` (`pengyos-install`); GRUB
  gains the `smbios` module (and `test` on Core). Existing installs get it on
  the update after this one, when the new updater rewrites `grub.cfg`.
- **No boot splash on Desktop NVIDIA.** Plymouth ignores simpledrm unless told
  otherwise and waits for a GPU driver; nvidia-drm loads seconds after Plymouth
  starts (no initramfs), so Pengy barely showed. `plymouthd.conf` sets
  `UseSimpledrm=1`: Pengy shows on the firmware framebuffer from ~9 s, then on
  nvidia-drm. It still blanks briefly while nvidia-drm takes over; a steady
  splash needs an initramfs with early KMS.

### Fixed (26.10.6)
- **Quiet speakers and headphones on the Desktop** (a ThinkPad T14 Gen 1 AMD,
  diagnosed by Pengy; the N100's ALC897 too). WirePlumber drives PCI sound
  cards as plain ALSA PCMs (`51-alsa-ucm.conf`, so HDMI outputs are sinks of
  their own), and on that path PipeWire only does software volume: the
  codec's hardware `Master` stayed at its power-on level, -20 dB on the
  ALC257, so 100% in Settings was quiet. `pengyos-alsa-unity.service` sets
  each PCI card's `Master` and `PCM` to 0 dB at boot, after alsa-restore.
  The override's comment also claimed `use-acp=false` selected UCM; it
  bypasses ACP and UCM (alsa-ucm-conf has no profile for a plain HDA card),
  and now says so.

### Fixed (26.10.5)
- **Minutes-long black-screen boots on a ThinkPad L14 Gen 1 AMD** with its
  "Security Chip" (TPM) turned off in the BIOS: every image (Desktop, Server,
  installed slots, sticks) put a serial console on the kernel command line
  (`console=ttyS0,115200`), and with that firmware setting the serial console
  made boot crawl for minutes (and, as `/dev/console`, blocked systemd behind
  a black screen; Ctrl+Alt+Del did nothing). Ubuntu, with no serial console,
  booted fast; removing the argument made ours boot in 12 s. GRUB now adds the
  serial console only when its serial driver finds a real UART at COM1 (the
  scratch-register test, which QEMU's passes and a phantom port fails): the
  board `grub-efi.cfg`s, and the installed `grub.cfg` that `pengyos-install`
  writes (`KERNEL_ARGS` no longer has it). GRUB gains the `serial` module.
  Existing installs get the change through a new update step,
  `update.d/40-serial-console`, which swaps the fixed argument for
  `${pengyos_serial}` before the updater re-reads the kernel arguments.
- **Screen brightness is kept across reboots** (Desktop): `systemd-backlight`
  was never built, so laptops started at the firmware's dim default each boot.
- **LVM volume groups autoactivate**: lvm2's udev rule ran `systemd-run` from
  the build host's `output/<target>/host/bin/` (configure found it on `$PATH`),
  a path no machine has; it also made images differ by output directory. lvm2
  is configured with `/usr/bin/systemd-run`, and the core post-build now fails
  if any udev rule carries a build-host path.
- `pengyos-qemu-verify` 0.3.1: `--tpm` attaches a TPM device to the guest (it
  only ever attached the swtpm backend, so the "uefi-tpm" smoke cases had no
  TPM).

### Added (26.10.5)
- **Server NVIDIA** (`pengyos-server-nvidia`, `IMAGE_ID=pengyos-server-nvidia`):
  the Server plus NVIDIA's driver for headless GPU work (US-SRV-11), a fifth
  image instead of the `systemd-sysext` extension US-ALL-2 planned, which is too
  far out. A new `nvidia-compute` layer carries it (core + server +
  nvidia-compute); the Desktop NVIDIA stacks the old `nvidia` layer on top of
  it. In the image: the open kernel modules, GSP firmware, CUDA, NVML
  (`nvidia-smi`), NVENC/NVDEC, the MPS daemon, `nvidia-persistenced` (started by
  udev when the nvidia module loads) and the `cuda` dev box. Its own update
  feed and installer stick; `release.sh` builds it by default and checks it
  with the Server smoke matrix (a VM has no NVIDIA GPU; CUDA is checked on
  hardware).
- **GPUs in podman containers** on both NVIDIA editions: a recipe for
  `nvidia-container-toolkit` 1.20.1 (CDI only: `nvidia-ctk` and
  `nvidia-cdi-hook`) whose refresh unit writes `/var/run/cdi/nvidia.yaml` when
  the nvidia module loads, so `podman run --device nvidia.com/gpu=all …` works,
  rootless too. The images gain `ldconfig`, which the spec's ldcache hook runs.

### Changed (26.10.5)
- `nvidia-open` splits into a compute core and a **graphics option**
  (`BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS`: EGL/GLES, GBM, the Wayland platforms,
  PRIME offload; needs Mesa GBM, Wayland, libglvnd), so it builds for a Server
  without them. Vulkan now depends on graphics. New options: the persistence
  daemon (`_PERSISTENCED`, on for Server NVIDIA only: it would keep a laptop's
  dGPU powered). Both NVIDIA images gain `nvidia-cuda-mps-control/-server`.
- `pengyos-desktop-nvidia lint` 0.3.0 checks both NVIDIA defconfigs and
  targets; `pengyos-release` 0.9.0 lists Server NVIDIA on the download page.

### Fixed (26.10.4)
- **The Desktop release check proves the desktop runs.** `release.sh` now
  boots each Desktop installer stick under QEMU (virgl in Xvfb) with
  `scripts/qemu-desktop-check.py` and requires a working root console login,
  cosmic-comp, cosmic-panel and the installer GUI, and no failed units; the
  old check stopped at a login prompt, which 26.10.3 passed.
- **Console logins work on Server and Desktop again** (TTY and serial). 26.10.3
  rebuilt shadow with PAM support, which installed `/etc/pam.d/login`,
  `passwd`, `chsh` and friends; each includes a `system-auth` stack nothing
  provided, so every console login answered "Permission denied" (graphical
  and SSH logins were fine). PengyOS now ships `/etc/pam.d/system-auth`.
- **USB sticks and flashed images boot on more PCs: the ESP is FAT32.** At 64
  MiB, `mkfs.fat` chose FAT16 for the boot partition of every flashed image and
  installer stick, and some UEFI firmware won't boot from a FAT16 ESP: the
  screen stays black and the machine resets to POST (a Lenovo ThinkPad L14 Gen
  1 AMD; likely the "black screen on a mini PC" reports too). `-F 32` forces
  FAT32, and the ESP now starts at 1 MiB, the usual alignment. Installed
  systems already had a FAT32 ESP from the installer.
- **26.10.3's Desktop images could not start the desktop.** cosmic-comp needs
  `libinput.so.10`, which no package selected any more: it had arrived with
  Weston, and survived Weston's removal (10-01) only as a stale file in the old
  build trees. 26.10.3's builds refreshed the trees, the library was gone, and
  the compositor exited (127) at every start; greetd and the console login
  still came up, so the QEMU check passed. behoweather installed it from
  testing and was rolled back to 26.10.0 by its A/B slot. Fixes: libinput is
  selected in the desktop layer, and every edition's post-build now ends with
  `board/common/check-needed-libs.py`, which fails the build if any ELF in the
  image needs a shared library the image doesn't contain.

### Removed (26.10.3)
- **`buildroot-external/targets.toml`**, a duplicate of the repo-root
  registry that only `pengyos-desktop-nvidia lint` read (it now reads the root
  one).
- **The parked UKI / sysupdate tools**: `pengyos-uki`, `pengyos-bootchain`,
  `pengyos-bootcount` and `pengyos-sysupdate`, built for the
  systemd-boot/sysupdate design that "A/B lite" (`pengyos-update`) replaced.
  No image used them; they are in git history if that design comes back.
- **The Ralph-loop scaffolding**: the per-tool `implementation.md`, `prompt.md`
  and `AGENTS.md` working files, the task tables and generators in `scripts/`,
  `docs/RALPH-PLAN.md`, the verification-ladder docs, `docs/close-core-456.md`,
  and the three spec-only placeholder tools (`pengy-launcher`, `pengy-osd`,
  `pengyos-desktop-steam`). They were intermediate design notes for work that
  has since landed; each tool's `spec.md` and README remain its contract.
- **`pengyos-core-smoke-m2`, `pengyos-server-smoke`, `pengyos-smoke-runner`**:
  their stale matrices (an M2-era Core log, a "Server 0.1.0" banner) are
  replaced by `pengyos-qemu-verify`'s own `smoke/core.toml` and
  `smoke/server.toml`, grounded in fresh 26.10 boots.
- **The M1 build** (`pengyos-core-m1`): the busybox "boot to a console"
  milestone, its `board/qemu-x86_64`, defconfig, `scripts/build-m1.sh`,
  `scripts/qemu-test-m1.sh` and the `dist/` README. Core (the systemd base)
  replaced it long ago.
- **Seven one-shot wiring tools**: `pengyos-{m2,server,desktop}-defconfig`
  (stale copies of what is now in `buildroot-external/`), `pengyos-desktop-av`,
  `pengyos-desktop-amd-prime`, `pengyos-server-clis-wire` and
  `pengyos-desktop-build`. Their output lives in the defconfig layers and board
  overlays; the gate keeps `pengyos-desktop-nvidia lint` as its wiring check.
- **Weston and foot** from the Desktop: the compositor and terminal of the
  pre-COSMIC bring-up, still installed (three "Foot" entries in the App
  Library, Weston's session file deleted at build time). Nothing else depended
  on them; the libraries they pulled in are still selected by Xwayland and
  COSMIC's dependencies.

### Changed (26.10.3)
- **The nightly maintenance loop, phase 1** (`tools/pengyos-nightly`): every
  evening Buildroot's `pkg-stats` (CVEs, upstream versions) for each edition,
  kernel.org, COSMIC/NVIDIA/Buildroot tags and the CISA KEV / EPSS feeds are
  checked; new CVE matches are triaged P0/P1/P2 by the local model (rules for
  host-only and KEV); an HTML report is emailed when something is new. Nothing
  is built or published by it yet.
- **CI**: `.github/workflows/gate.yml` runs the full gate
  (`scripts/pengyos_gate.py --mode full`: every tool's `verify.toml`, the wiring
  lints, recipe source checks) on every push and pull request. Image builds and
  QEMU boots stay on the build host.
- **One project URL, `https://pengyos.catbee.ca`**: os-release's `HOME_URL`
  (plus a new `DOCUMENTATION_URL`, `/docs/`), the login motd, and the SBOM
  namespace (was `sbom.pengyos.cat`) use it; tool READMEs no longer point at
  `pengyos.org`, `pengyos.catbee.net`, `catbee.ai` or `www.catbee.ca`.
- **PengyOS scripts out of `/usr/local/bin`**, the admin's directory: the
  Server's backup and monitor scripts and the Desktop's Flatpak and browser
  first-boot setup move to `/usr/libexec/pengyos/` (run by their units, not by
  hand), and `pengyos-dri-prime` to `/usr/bin/`.
- **Targets, defconfigs, boards and images are named after their edition.**
  `pengyos-core-m2` → `pengyos-core` and `pengyos-desktop-bringup` →
  `pengyos-desktop` (targets and `pengyos_*_defconfig`); `board/qemu-x86_64-m2`,
  `-server`, `-desktop` → `board/core`, `board/server`, `board/desktop`; images
  `pengyos-core-x86_64.img`, `pengyos-desktop-x86_64.img` and
  `pengyos-desktop-x86_64-installer.img`. The GRUB entry on Core reads
  "PengyOS Core". `IMAGE_ID` and the published download names are unchanged.
- **Flatpak is built from source** (`package/flatpak` 1.18.4, with new recipes
  for `appstream` 1.2.1 and `libfyaml` 0.9.6, plus a target
  `desktop-file-utils`), instead of the build host's flatpak CLI, system helper
  and portal being copied into the Desktop. COSMIC Store now links the image's
  own libflatpak/libostree. The image also gains the `flatpak` system user the
  helper needs. Verified in QEMU: Flathub set up on first boot, its AppStream
  pulled, a runtime installed.
- **`BR2_REPRODUCIBLE` is on** for every edition (core layer): UTC/C locale,
  fixed kernel build user, host and time, `SOURCE_DATE_EPOCH` from the commit.
- `scripts/release.sh`: one command for configure → build → SBOM → boot check
  for any set of editions, with signing (`--publish`) and upload (`--push`) as
  explicit opt-ins.

- **The build host is reproducible from the repo.** `scripts/build-cosmic.sh`
  clones the COSMIC components pinned in `scripts/cosmic-components.tsv`, applies
  our patches and builds them plus the two PengyOS libcosmic apps; verified from
  an empty directory (26 cargo builds) into a Desktop image that boots to the
  greeter and, from the stick, into a full COSMIC session.
  `scripts/provision-build-host.sh` (was `provision-behoserver.sh`) is host
  neutral, and `--desktop` installs COSMIC's build dependencies, rustup, and the
  packages the Desktop's post-build copies files from. `run-qemu-disk.sh` finds
  images in the build output instead of `~/Personal/pengyos/dist`.
- **`SPEC.md` rewritten to describe what ships in 26.10**: Buildroot as the
  engine (no longer "undecided"), GRUB + A/B ext4 slots + btrfs `data` (UKI and
  systemd-boot marked parked), GNU coreutils and the reference D-Bus daemon with
  their planned replacements, no LSM yet, the real Core/Server/Desktop package
  sets, and an honest build-host section: what the Desktop takes from an
  Ubuntu 26.04 host. The M0–M3 milestone tables and the 2026-09 audit are gone.
- `scripts/pengyos_gate.py --mode full` runs each tool's `verify.toml`, so the
  six Rust tools are tested too (74 checks, ~3 minutes).
- Tool docs, docstrings and tests no longer point at the deleted Ralph working
  files; fixtures use generic user names and paths instead of the build host's.
- `pengyos-qemu-verify smoke` checks Core by default (and Server with
  `--matrix smoke/server.toml`) against the image `pengyos-build` just built;
  both editions pass on 26.10.
- The login-banner unit is "PengyOS login banner" on every edition (it said
  "Core" on Server and Desktop too).
- Hostnames match the editions: `pengyos-desktop` (was
  `pengyos-desktop-bringup`) and `pengyos-core` (was `pengyos-m2`).
- The Desktop post-build reads COSMIC from `$PENGYOS_COSMIC_SRC` and
  `$PENGYOS_COSMIC_BUILD` (default `~/cosmic-src`, `~/cosmic-build`) instead of
  hard-coded `/home/beholder/…` paths.
- Desktop layer comments rewritten where they described the bring-up or the
  retired `wire` tools rather than the image as it is.

### Fixed (26.10.3)
- **xdg-desktop-portal fetches its libglnx/gvdb subprojects as hash-checked
  tarballs** (Buildroot `EXTRA_DOWNLOADS`, same pinned revisions, identical
  contents) instead of `git clone` during the build: unverified, uncached and
  network-dependent at build time.
- **`/etc/issue` is plain text**: `PengyOS Desktop 26.10.3`, written at build
  time from `VERSION`. It was `\S{PRETTY_NAME} \n \l`, which agetty expands
  but `cat /etc/issue` printed verbatim.
- **WebUSB and WebSerial in Chrome** (Desktop): USB devices and USB serial
  ports are tagged `uaccess`, so the user at the seat gets read-write access
  (an ACL logind removes at logout). Chrome listed devices but could not open
  them; checked on behoweather (the seat user gets `rw` on its devices, hubs
  excluded).
- **Reproducible images across build hosts.** The first build on a second host
  (behoweather, in an Ubuntu 26.04 distrobox) matched behoserver's kernel bit
  for bit; the root filesystems differed in nine files and every disk image in
  its IDs. Fixed at the source: the SBOM's `created` time comes from
  `SOURCE_DATE_EPOCH`; the root password placeholder is given pre-hashed (a
  random salt changed `/etc/shadow`); lvm2 no longer records whether the build
  host has `modprobe`, ncurses whether it exports `TERMINFO_DIRS`, Python which
  `install` was on its PATH; and the rootfs UUID / hash seed, the GPT disk and
  partition GUIDs, the installer ESP's volume ID and the payload filesystem's
  UUID are derived from os-release (`board/common/image-ids.sh`) instead of
  picked at random. The Desktop comparison found more: NetworkManager recorded the
  host's polkit helper path, COSMIC app icons were picked in filesystem order
  (the largest size now wins), COSMIC data kept the host's umask (nothing under
  `/usr` is group-writable now), and the MIME index (`mimeinfo.cache`, the "Open
  with" lists) came from the host's `update-desktop-database` — missing in a
  fresh distrobox, so that image had none. Buildroot's host-desktop-file-utils
  builds it now. libclc's nvptx64 bitcode took its PTX version from a CUDA install on
  the build host (behoserver's `/usr/local/cuda`); clang is now pointed at none.
  Building 26.10.3 on both hosts then left one file in Core and Server:
  `functools.pyc`, whose bytes depended on what the single `compileall`
  process had compiled before it; each file is now compiled in its own
  interpreter. NetworkManager also recorded the host's `dnsmasq`. The ESP's FAT volume
  serial (genimage's `mkdosfs`) is derived too. The installer stick's ESP no longer
  carries the wall-clock time in its volume label (`mkfs.vfat --invariant`).

- **A hidden Buildroot edit:** behoserver's Buildroot checkout carried a hand edit
  to Mesa's recipe (`-Degl-native-platform=drm`), so any other host built a
  different Mesa. It is now an override in `external.mk`.
- **The Desktop never had an SBOM:** `legal-info` stopped at our
  xdg-desktop-portal recipe, which named a `LICENSE` file the source calls
  `COPYING`. Every recipe of ours now also carries its license file's hash.
- **Images carried a stale SBOM.** The post-build step embedded whatever SBOM sat
  in `images/`, so every Core image since 2026-09-17 shipped that day's SBOM
  (and, after the rename, under the old target name). It now clears the
  previously embedded files and refuses an SBOM older than the build's
  `.config`; `pengyos-build sbom` removes SBOMs left under other names.

### Verified on real hardware (2026-10-01)
- The built images boot and work on the fleet. **Desktop** on beholaptop (AMD
  Ryzen 7 6800H + Radeon 680M), behocraptop (Intel i3-1215U), behothinkpad
  (Intel i7-7600U), behobasement and behoweather (Intel N100); **Server** on
  behocraptop. The boot-splash handoff (simpledrm -> i915/amdgpu), I2C
  touchpads, the AMD ACP digital mic, Wi-Fi firmware (8265/rtw88), Intel SOF
  audio, sensors/EDAC, the login screen + auto-login, USB game pads, driverless
  printers (ipp-usb), Bluetooth codecs and Steam controllers all behaved. The
  **NVIDIA edition, NVIDIA Vulkan/X11 GL and discrete-GPU launch remain
  unverified on metal** — beholaptop ran the plain Desktop image.

### Changed (26.10.2)
- **PengyOS Updates asks when to restart.** An available update now offers
  **Update and restart** (installs, then restarts by itself; *Don't restart*
  cancels that while it installs) or **Update, restart later**, instead of a
  single *Update now*. The app is also pinned to the Desktop's dock by
  default (it was only in the App Library). Desktop and Desktop NVIDIA only.

### Fixed (26.10.1)
- **Images carried the maintainer's SSH key for root.** Core's post-build
  only ever *added* `/root/.ssh/authorized_keys` (when
  `PENGYOS_AUTHORIZED_KEYS_FILE` is set), and Buildroot's `target/` keeps
  files between builds, so a key injected once stayed in every later image,
  public downloads included. Post-build now removes it when no key is asked
  for. Installed machines keep their own `/root` (it lives on the data
  partition), so updating doesn't remove keys a machine already has.

### Fixed (26.10.0)
Found by running the new website recipes (pengyos.catbee.ca/docs) on the
26.09.4 Server and Desktop images in QEMU, as an ordinary user.
- **The nightly backup failed on every Server and Desktop.**
  `pengyos-backup.sh` runs `tar -czf … --ignore-failed-read`; the image's
  `tar` was BusyBox's, which has neither. GNU tar is now in the core layer
  (BusyBox defers to it), which also fixes every upstream
  `curl … | tar xz` install line (uv, Go, Syncthing).
- **Rootless podman could not publish ports from a user-defined network**
  (`podman network create` + `-p`, so every podman-compose project with
  `ports:`): Buildroot builds only `cmd/podman`, not the `rootlessport`
  helper. `external.mk` now builds it into `/usr/libexec/podman`.
- **No `ldd`**: uv's `install.sh` probes glibc with it and took a broken
  fallback path. Installed from glibc's build tree (like `getent`).
- **No FUSE on the Server** (`/dev/fuse` missing, `rclone mount` failed):
  the server layer enables libfuse3, which turns on `CONFIG_FUSE_FS` and
  ships `fusermount3`.
- Backup file names ended in a literal `SS` instead of the seconds
  (`date +%H%MSS`).

### Fixed (26.09.4)
- **XDG autostart never ran in the Desktop session** (`/etc/xdg/autostart`,
  `~/.config/autostart`: the update notifier, and any app set to start at
  login): cosmic-session leaves autostart to systemd whenever systemd is PID 1,
  but `cosmic-session.target` isn't installed and our session bus is
  dbus-run-session's, not the systemd user bus. start-cosmic now runs
  `/usr/libexec/pengyos/session-autostart` on the session bus once the
  compositor is up (spec rules: user overrides, Hidden, OnlyShowIn/NotShowIn,
  TryExec).
- Writing an update no longer leaves gigabytes of the new slot in the page
  cache (seen: 4.7 GB peak on behoweather); the app follows an install that
  was started elsewhere.

### Added (26.09.3)
- **PengyOS Updates** (Desktop, `tools/pengyos-updates`): an app that shows
  what is available with its notes, installs it on request with a progress bar
  (into the other slot; takes effect at restart), restarts, goes back to the
  previous version, and switches the testing channel — plus a session notifier
  that announces an update or a pending restart only as the user wants:
  notifications on/off, "remind me" tomorrow / in 3 days / a week / a month
  (a newer release still gets through), at most once a day. Administrators
  need no password for these (polkit rule for the `pengyos-update-*` units).
- **Daily check** for every installed machine (`pengyos-update-check.timer`):
  records the answer in `/var/lib/pengyos-update/state.json` and, on Servers,
  in the login message (`/run/motd.d`). Installing is always a choice.
- **Release update steps**: before switching, `pengyos-update apply` runs the
  new image's `/usr/lib/pengyos/update.d/*` as root (they are part of the
  signed image). The first, `50-grub-efi`, brings the image's GRUB and font to
  the boot partition (old copies kept as `*.prev`), so GRUB fixes can ship.
- **Downloads** at <https://pengyos.catbee.ca/download/>: Desktop, Desktop
  NVIDIA, Server (installer sticks) and Core (disk image), `.img.xz`, with
  signed `SHA256SUMS` and instructions for writing and booting
  (`pengyos-release download-publish`).

### Fixed
- **26.09.2:** `pengyos-update confirm` (and `apply` / `status`) failed on a
  booted system because systemd's GPT auto-generator had the ESP on `/efi`
  already and a second, read-only mount was refused ("would change RO state");
  it now uses the existing mount. 26.09.1 machines can update to 26.09.2 after
  `sudo umount /efi`.

### Added
- **Updates** (Server, Desktop, Desktop NVIDIA; first release over the air:
  26.09.1, tested on behoweather 26.09.0 -> 26.09.1 -> 26.09.2). Releases are announced in an Atom feed at
  <https://pengyos.catbee.ca/update/> (`feed.xml`, readable in any feed reader)
  with channels **stable** and **testing**. Installed machines run
  `sudo pengyos-update apply`: it checks the release's `manifest.json` against
  the ed25519 key the image ships (`/usr/share/pengyos/keys`, signed with
  `pengyos-sign`'s format; the private key stays off the repo), writes the new
  system into the *other* root slot, and GRUB boots it once
  (`pengyos_try`); `pengyos-update-confirm.service` keeps it when it comes up,
  otherwise the next boot is the old system. `status`, `check`, `rollback`,
  `channel` too (`tools/pengyos-update`). Publishing:
  `pengyos-release update-publish` / `update-promote`. The image gains the
  `openssl` command (signature checks). Preinit now copies new build-time
  `/var` content from a new image into `data`, once per image.
- **Versioning: `YY.MM.N`** (calendar versions, like Ubuntu/NixOS/Pop!_OS),
  from one `VERSION` file (starts at `26.09.0`). os-release carries
  `VERSION_ID=26.09`, `IMAGE_VERSION=26.09.0`, `IMAGE_ID=pengyos-<edition>`
  (the NVIDIA image is `pengyos-desktop-nvidia`, "PengyOS Desktop NVIDIA", so
  updates can't cross over) and `BUILD_ID` (the git commit); written by one
  shared script (was `0.1.0` hard-coded three times, "Core, M2"). The installer
  stick's payload carries the same version (was `git describe`).
- **A login screen on the Desktop** (greetd + COSMIC's greeter, as Pop!_OS):
  installed systems start at it and ask for the password. **Automatic login
  is opt-in**, like Ubuntu's: "Log in automatically" in the installer (GUI and
  console; off by default), or `sudo pengyos-autologin on|off` later (also
  AccountsService's SetAutomaticLogin: the setting is
  `/etc/pengyos/autologin.conf`, GDM's format). Logging out returns to the
  login screen. The live stick still logs straight in. The session now starts
  from `/usr/libexec/pengyos/start-cosmic` (was `cosmic-session.service`).
  QEMU: install, login screen, wrong password refused, right one opens pat's
  desktop; auto-login on → straight in after reboot; logout → login screen.
- **Desktop parity, tier 2** (Desktop + NVIDIA; QEMU-tested):
  - COSMIC Settings' **Users** page lists and edits accounts (new package
    `accountsservice`, plus glibc's `getent`, which Settings runs and Buildroot
    never installed: every edition) and **Region & Language** works
    (systemd-localed). Both pages were empty.
  - "Launch using discrete graphics" on hybrid machines (new package
    `switcheroo-control`, started at boot).
  - VPNs (NetworkManager OpenVPN plugin, `wg`) and mobile broadband
    (ModemManager).
  - Printers: driverless USB printers/scanners (new package `ipp-usb`), and
    Gutenprint + HPLIP drivers for older ones (CUPS lists ~5,800 models).
  - Chinese/Japanese/Korean text (new package `font-noto-cjk`).
  - Video and PDF thumbnails in COSMIC Files (new package `ffmpegthumbnailer`;
    a poppler-based PDF thumbnailer in the overlay).
  - Bluetooth audio codecs AAC, aptX/aptX HD, LDAC and LC3 (new packages
    `libfreeaptx`, `ldacbt`).
  - An ssh-agent in the session (`SSH_AUTH_SOCK` for every app) and gpg's
    terminal passphrase prompt.
  - Rootfs Desktop 4000M, NVIDIA 4600M (8 GiB slots).
- **Media playback** (Desktop): GStreamer's good/bad plugins, gst-libav (FFmpeg
  decoders), dav1d (AV1), VA-API decode, plus the `ffmpeg`/`ffprobe` and
  `gst-*` tools. COSMIC Player could play nothing (no `appsink`, no demuxers
  or decoders). QEMU: H.264/AAC MP4, VP9/Opus WebM, AV1 MKV, MP3 and FLAC all
  decode; COSMIC Player plays the MP4 to the end.
- **Swap on zram** (every edition; new package `zram-generator`): zstd,
  min(RAM, 8 GiB), Pop's VM tuning; util-linux's `mkswap`/`swapon` replace
  BusyBox's (systemd drives them). There was no swap at all. **earlyoom** on
  the Desktop kills the worst offender before the machine freezes (not
  systemd-oomd: the session is one cgroup).
- **Gaming**: Valve's controller/VR udev rules (`steam-devices`, uinput loaded
  at boot) and GameMode (`gamemode`, behind the portal Flatpak games use).
- **Intel laptop audio**: the SOF kernel driver (TGL … PTL, SoundWire, DMIC)
  and firmware (`sof-firmware`); snd-intel-dspcfg keeps HDA where SOF isn't
  needed.
- **`.local` names** resolve (nss-mdns).
- **Firefox out of the box**: installed from Flathub on the first boot with a
  network (`pengyos-browser-setup`, retried until it succeeds; not on the live
  stick), and the default for web links. QEMU: installs, and the Printers
  launcher opens it on the CUPS page.
- **The portals COSMIC doesn't implement** (new package
  `xdg-desktop-portal-gtk`, with GTK3; what Pop!_OS ships): Flatpak apps can
  open links and files in other apps again (OpenURI, which xdg-desktop-portal
  offers only with an "Open with" backend), keep the screen awake (Inhibit),
  print, notify, and add launchers (DynamicLauncher). COSMIC's backend still
  answers file pickers, screenshots, screen sharing and settings. QEMU: an
  OpenURI request shows the "Open With…" dialog. About 17 MB.
- **A keyring (the Secret Service)** on the Desktop: new package `oo7`
  (0.7.0-beta, the Rust replacement for gnome-keyring), with its Secret portal
  for Flatpak apps. Browsers, VS Code, the GitHub CLI and anything using
  libsecret can now keep passwords and tokens; there was nothing providing
  `org.freedesktop.secrets` before. The desktop logs in without a password, so
  the keyring's password is random, sealed with `systemd-creds` (TPM, else the
  host key) on first use and unsealed when an app first asks (`oo7-session`).
  QEMU: a secret stored, the daemon restarted, the keyring unlocked with no
  prompt and the secret read back.
- **Mouse cursors** (new package `pop-cursor-theme`: COSMIC's Pop cursors, as the
  system default theme). There was no cursor theme, so cosmic-comp drew one
  built-in arrow for every shape (no text I-beam, no resize arrows) and X11
  apps got the bare X cursor.
- **X11 apps run on the Desktop (Xwayland)**, both editions: Steam and games,
  and any app that only speaks X11. cosmic-comp starts Xwayland at login and
  X11 windows sit in the Wayland session; there is still no Xorg server. It
  draws on the GPU (glamor) with GLX for OpenGL apps; Mesa now ships as a
  libglvnd vendor on every Desktop (GLX next to VA-API needs it).
- **NVIDIA Vulkan on the NVIDIA Desktop.** NVIDIA ships its Vulkan driver in
  libGLX_nvidia, which needs the X.org client libraries; they are in the image
  now, for Xwayland. Before this, Vulkan found only lavapipe on NVIDIA machines,
  so COSMIC's apps rendered on the CPU. Also installs NVIDIA's GLX vendor library
  and its X11 EGL platforms, so X11 apps under Xwayland use the NVIDIA GPU.
- **The dev box** (`pengyos-guest` 0.2.0, Server layer and up). Compilers and
  SDKs live in a distrobox guest that shares your home: `pengyos-guest enter dev`
  (Ubuntu 24.04: build-essential, cmake, ninja, git, go, python3, rustup, uv) is
  created on first use; `enter dev -- CMD` runs one command. NVIDIA images add
  `cuda` (`nvidia/cuda:13.1.0-devel-ubuntu24.04`, host driver shared).
  Definitions live in `/usr/share/pengyos/guests.d`, overridable from
  `~/.config/pengyos/guests.toml`. The tool itself was reworked to drive real
  distrobox correctly (it had never run against one) — see its CHANGELOG.
- **Qt apps look native in COSMIC** (new package `cutecosmic` 0.2.0, the Qt
  platform theme for COSMIC): Pengy and any other Qt app follow COSMIC's
  dark/light mode, accent colours, fonts, icon theme and file dialogs; Qt loads
  it automatically in the COSMIC session. Two patches: Qt Quick Controls is
  optional (it only styles QML apps; the image has no qt6declarative), and the
  Rust bindings (libcosmic) are built offline by Buildroot's cargo instead of a
  Corrosion checkout fetched at configure time. Uses private Qt APIs: rebuild it
  with Qt.
- **Pengy, the built-in agent** (new package `pengy`, PengyR v1.10.0): one Rust
  agent core behind three front ends — `pengy-cli` in every edition (Core),
  `pengy-web` from Server, and the Qt6 desktop app on the Desktop, pinned first
  in the COSMIC dock on the live stick and installed systems (dock favorites
  default; the window's Wayland app_id `pengy` matches `pengy.desktop`). The CLI
  and web UI are pure Rust (glibc only); the GUI links the Rust core statically
  and uses system Qt 6.11 (Widgets/Network/Svg + qt6wayland), which enters the
  Desktop image only. Ships unconfigured: each user points it at their model.
- **COSMIC System Monitor** (`pop-os/cosmic-monitor` epoch-1.9.0): a task
  manager / resource view, host-built like the other COSMIC apps. It needs rustc
  >= 1.95 (Ubuntu ships 1.93), built with a per-user rustup toolchain.
- **System sounds.** New package `cosmic-sound-theme` (epoch-1.9.0) plus
  Buildroot's `sound-theme-freedesktop`: notification, volume, battery and
  plug sounds. `/usr/share/sounds/Pop` points at it, since COSMIC 1.9 looks its
  battery sounds up under that name. Before, the image had no sound theme.
- **COSMIC Viewer is the image viewer.** System76's `pop-os/cosmic-viewer`
  (epoch-1.9.0) is built on the host like the other COSMIC apps and installed by
  the Desktop post-build; `mimeapps.list` makes it the default for images and
  adds AVIF/HEIF/JXL/SVG/ICO/QOI. (todo #86)
- **Boot splash.** New package `plymouth` (26.134.222) with a PengyOS theme:
  Pengy on black with a spinner, from early userspace until the COSMIC session
  takes the screen. The session hands over like GDM (`plymouth quit
  --retain-splash`, after the seat/output wait), so the splash covers the part of
  boot that used to be a black screen. `splash plymouth.ignore-serial-consoles`
  on the live and installed kernel command lines.
- **`xdg-utils`** (`xdg-open`, `xdg-mime`, ...; todo #87). Not a Buildroot
  package: our recipe renders the scripts' help text with a small DocBook-to-text
  converter (upstream needs `xmlto` + a text browser) and patches xdg-mime's
  generic backend (the one COSMIC gets) to ask `gio` for file types, since the
  image has no `file`/`mimetype`. `open` now delegates to it.
- **`systemd-analyze`** in every edition, to measure real-hardware boots.
- **Kernel: fleet survey drivers** (Core fragment, so Server gets them too):
  k10temp, nct6775, i2c-piix4, AMD CCP/PSP, EDAC (AMD64, IGEN6), Intel int340x /
  PCH thermal, powerclamp, uncore-freq, VSEC, SPI-NOR flash. Desktop: AMD ACP
  (Renoir/Cezanne and Rembrandt digital mics). Firmware: iwlwifi-8265.
- **`scripts/check-kernel-fragments.py`** replays Buildroot's kernel config
  flow per edition and lists fragment lines Kconfig silently dropped.
- **The NVIDIA edition gets a driver that can run it.** New in-tree package
  `nvidia-open` (595.104.02, production branch): NVIDIA's open GPU kernel
  modules (`nvidia`, `nvidia-modeset`, `nvidia-drm`, `nvidia-uvm`) and the
  matching proprietary userspace, both from one `.run` so they cannot drift —
  EGL/GLES + GBM backend + the Wayland EGL platforms (what cosmic-comp needs),
  CUDA + NVML/`nvidia-smi`, NVENC/NVDEC, the GSP firmware, the suspend/resume
  units, runtime-PM udev rules, `prime-run`, and a nouveau blacklist. Supports
  Turing (RTX 20) and newer — the fleet's RTX 3060 Ti/3070/3070 Ti/5060 Ti. The
  `nvidia` layer now selects it plus libglvnd (Mesa becomes a glvnd vendor, so
  both GPUs' EGL coexist) and a 4000M rootfs. Host Vulkan is an opt-in package
  option: it needs the X.org client libs, which would switch on X11 backends
  across the Desktop; Flatpak apps get NVIDIA GL/Vulkan from Flathub's matching
  GL extension. Verified: builds against 7.2.5, all NEEDED libs resolve, and a
  QEMU boot runs the full COSMIC session on Mesa through glvnd; real-GPU
  bring-up is bare-metal work. Replaced: the layer's Buildroot `nvidia-driver`
  + modular X.org (that package is the legacy 390 branch — no Turing+, no GBM,
  does not build on 7.2), and the NVIDIA files every Desktop image carried in
  its overlay (a non-existent `nvidia-drm GSP-firmware` option, a udev rule
  matching attributes PCI devices do not have, `prime-render-offload`).
  `pengyos-desktop-nvidia` is now just the containment lint.
- **A macOS-style `open` command.** `open <file|url>` opens with the user's
  default app — a board-overlay script `usr/bin/open` that delegates to
  `gio open` (falling back to `xdg-open` if xdg-utils is ever added; see todo
  #87). `xdg-open` itself isn't in the image because `xdg-utils` is not a stock
  Buildroot package, and `gio open` already resolves the XDG defaults.
- **The Server installs like the Desktop (step 3).** `pengyos-server-x86_64-
  installer.img` is the Server image plus the install payload; booting it opens
  the console installer on tty1 (`/etc/profile.d/pengyos-installer.sh` on root's
  autologin, live boots only; over SSH/serial run `pengyos-install-tui install`).
  Installed Servers get the partition & state layout: read-only root slot,
  btrfs data partition, `/etc` overlay, account merge. The layout pieces
  (preinit, merge-accounts, efivarfs, sudo, the engine + TUI, sgdisk, btrfs /
  overlayfs / efivarfs built in) moved from the Desktop layer to the Server
  layer; the payload + stick assembly is shared (`board/common/
  installer-image.sh`). The installer takes an **SSH public key** for the user
  (TUI prompt, `pengyos-install image --authorized-keys FILE`) — the Server's
  sshd is key-only — and writes the COSMIC auto-login drop-in only on images
  that have COSMIC. **PAM + logind moved to the Server layer** and sshd uses
  PAM (still key-only): SSH logins are now logind sessions, which rootless
  podman needs — distrobox over SSH failed with "rootless needs no limits + no
  cgrouppath" on the Server (and would have on the Desktop).
- **Overlays and post-build scripts stack too; Server is a build box.** The
  rootfs overlays and post-build scripts now accumulate across layers like the
  kernel fragments (Core's, then Server's, then Desktop's), so Desktop's overlay
  lost 22 copies of Server files and its post-build lost its copies of Core's
  steps. Core's post-build is the shared one: SSH key injection, no baked host
  keys, provenance, SBOM, ensurepip (every edition's `python3 -m venv` works).
  **The developer set moved down to the Server layer** (US-DEV-1/2): pip, zsh,
  git, curl, distrobox, util-linux's tools, the rootless-podman defaults.
  Verified on the Server image in QEMU: venv + pip, uv, and gcc inside an
  Ubuntu 24.04 distrobox with the binary running on the host.
- **Layered defconfigs: Core ⊆ Server ⊆ Desktop is now enforced.** Each
  layer is written once (`buildroot-external/configs/layers/{core,server,
  desktop,nvidia}.config`) and the edition defconfigs are generated by stacking
  them (`scripts/compose-defconfigs.py`; a pengyos-build test fails on a stale
  one). The hand-kept copies had drifted: **Server** had lost all of Core's
  firmware (Wi-Fi, i915, the Realtek NIC), Python and the disk tools; **Desktop**
  lacked Server's CLI set (bat, eza, fd, zoxide), procps, rsync and several of
  Core's Wi-Fi firmware; the **NVIDIA** variant was an old Desktop snapshot with
  none of the hardware, audio or installer work. Kernel fragments stack too
  (Core's, then Server's, then Desktop's), so Desktop gains Core's NIC, RAID,
  MMC and dm-crypt drivers — incl. `IGB` for behoserver's I211. Resolved
  `.config`s were diffed against the previous builds: Core is unchanged,
  nothing was removed from any edition.
- **btrfs data partition.** The installer formats `data` as btrfs
  (`compress=zstd:1`, `noatime`) with one subvolume per area (`etc`, `var`,
  `home`, `root`), so machine state can be snapshotted and rolled back per area,
  which the agent's reversible Tier-1 changes need (USE-CASES US-AGT-2). btrfs
  is built into the kernel because preinit mounts `data` before modules load.
  Preinit now reads the filesystem type and label from the superblock itself:
  busybox `blkid` does not know btrfs, and on the first build this silently
  booted with a tmpfs `/data`. Existing ext4 installs still boot.
- **The developer (US-DEV-1/2, new stories).** A complete Python 3.14 (ssl,
  sqlite3, lzma/bz2/zlib, readline, curses, decimal, CJK codecs) + pip +
  setuptools, and `ensurepip`, which Buildroot leaves out and without which
  `python3 -m venv` fails. zsh is the installed user's shell. git, curl,
  util-linux's standard tools (`rev`, `findmnt`, `lsblk`, a btrfs-aware
  `blkid`). **distrobox** (our package, 1.8.2.5) is the compiler lane: Buildroot
  cannot ship a native toolchain. Rootless podman works for the installed user:
  an empty `/etc/subuid`/`subgid` makes `useradd` allocate ranges, and container
  logs go to files (`k8s-file`) because distrobox waits on `podman logs`, which
  came back empty under journald. A patch fixes distrobox's `ro:rslave` volume
  option on read-only roots. Verified in QEMU on an installed NVMe: venv + pip,
  `pip install --user uv` + `uv venv`, gcc and rustup/cargo inside an Ubuntu
  24.04 distrobox (binaries run on the host), the Go 1.27 tarball natively.
- **USE-CASES:** the developer persona reworked; US-DEV-1 (Python is part of the
  OS), US-DEV-2 (C/C++, Rust, Go), US-DEV-3 (self-hosting, with Ubuntu as the
  builder for now), and US-AGT-2 (the agent on the box: snapshot, mutate,
  converge back into the repo).
- **The desktop runs as the installed user, not root.** The installer's account
  is now the desktop's: `cosmic-session.service` opens a PAM session
  (`PAMName=pengyos-session`, auto-login) and the installer points it at the
  created user (`User=` drop-in); the stick stays root. `pam_systemd` makes it
  a real logind session on seat0 (`/run/user/UID`, the user's systemd manager,
  lock-session). The user is created with shadow `useradd` in `wheel`, `sudo`,
  `seat` (seatd), `video`, `audio`, `input`, `render`; `sudo` and a polkit admin
  rule make `wheel` the admins (they authenticate with their own password).
  Image gains Linux-PAM, sudo and a `seat` group (`board/…/users.table`).
- **Wi-Fi carries over from the live session.** Network profiles made on the
  stick (joined from the panel) are copied into the installed system, root-only,
  so it is online on first boot. No Wi-Fi page in the installer.
- **Partition & state layout (Desktop).** Installed disks are now ESP + root-a +
  root-b + data (`docs/state-layout.md`). The system boots read-only from a root
  slot; `/usr/lib/pengyos/preinit` (kernel `init=`, no initramfs) mounts the data
  partition, overlays `/etc` with the machine's changes (account files merged so
  newer images can add system users) and binds `/var`, `/home`, `/root` from it.
  Replacing root-a with a fresh image keeps hostname, accounts, SSH host keys,
  NetworkManager profiles, `/etc` edits and `/home` — the prerequisite for an
  update mechanism (not built yet; neither is boot-count fallback). GRUB gains
  `loadenv` + `test`; the stick's payload is now `rootfs.ext4.zst` +
  `esp.vfat.zst` (manifest `pengyos-layout-1`).
- **Desktop installer USB stick.** The Desktop build now also emits
  `…-installer.img`: the Desktop image plus a `PENGYOS-PAYLOAD` partition holding
  the release's rootfs and ESP (zstd + sha256 manifest). Booted, it opens the libcosmic
  installer (`pengyos-live-installer.service`); `pengyos-install-tui install` is
  the console equivalent. Both run the new `pengyos-install image` path: verify
  the payload, partition the disk (layout entry above), stream the ESP and
  rootfs into place (hash-checked), then hostname, first user, and user + root
  passwords (random per-account salts). Verified end-to-end in QEMU with both
  front-ends: the installed disk boots on its own. The image gains python3,
  zstd, sgdisk and e2fsprogs for this.
- **Server edition SSH made operational.** The Server image's `sshd` is now a
  standalone service with build-time host keys (mirroring the Desktop board's
  2026-09-24 fix); the broken socket-activated design — which could not bind
  port 22 and left SSH unreachable — is gone.
- `CHANGELOG.md`, `CONTRIBUTING.md`, and directory-level `README`s across the
  repository (structural directories + every tool).
- Ralph-loop program documentation moved to `docs/RALPH-PLAN.md` (since removed).
- [`docs/qemu-testing.md`](docs/qemu-testing.md) — the boot/SSH/screenshot runbook
  (verification-ladder rung (e)).

### Changed
- **Hidden GRUB menu, 1 s.** Installed systems (`pengyos-install`) and the
  Core/Server/Desktop sticks boot after 1 s with no menu shown; hold Esc or Shift
  for it (the A/B slot entries on an installed disk). Was a visible 3 s / 5 s.
- `tools/pengyos-server-clis-wire` merged: the Server target ships the **stock**
  comfort CLIs (`fd`/`bat`/`eza`/`zoxide`); the seven authored recipes are
  deferred until they are build-verified.
- `MVP.md` retired — its milestone record is folded into this file (see `0.1.0`).

### Removed
- **swayimg removed** (package, layer entry, `mimeapps.list` fallbacks), with
  the WebP/GIF/TIFF libraries it alone pulled in: COSMIC Viewer decodes in Rust
  and replaces it. TGA images lose their viewer.
- ~3,300 files of leaked test scratch (`tools/pengyos-desktop-defconfig/{atest,btest,cptest,mktest}/`).

### Fixed
- **Chrome, VS Code and Steam (Flatpak) didn't start; Firefox, VLC, Discord
  did** (behoweather). The Flatpak portal (`org.freedesktop.portal.Flatpak`)
  wasn't staged with the rest of Flatpak: Chromium/Electron apps start their
  sandboxed processes through it (zypak: "Failed to initialize (is the Flatpak
  D-Bus portal working?)", VS Code windows crashed) and Steam its containers
  (exit 71, "requires ... flatpak-portal service"). Now staged, D-Bus-
  activated. Checked on behoweather with the portal started by hand: Chrome,
  VS Code and Steam all start; in QEMU the image's portal activates on demand.
- **Two printer launchers**: CUPS's "Manage Printing" duplicated our
  "Printers"; removed.
- **`/etc/issue` and the motd.** The console banner showed the hostname twice
  and a fake `Login:` line above the real prompt; it is now one line from
  os-release, Debian-style (`PengyOS Server 26.09 <host> ttyS0`). The motds
  were stale developer text (the Desktop's described a "home-lab node",
  Core's quoted `BR2_INIT_SYSTEMD=y`); now the edition, the web site and the
  commands worth knowing. They were also never shown over SSH (`PrintMotd no`
  without `pam_motd`): Server/Desktop use `pam_motd`, Core `PrintMotd yes`.
- **A timezone change took up to a minute to reach the panel clock**, and
  the lock screen logged "Failed to parse timezone" every minute
  (behoweather). Buildroot's tzdata puts the zones in `posix/` with top-level
  symlinks (`America -> posix/America`); jiff (COSMIC's time library) doesn't
  follow directory links when indexing zoneinfo, so `America/Toronto` "did not
  exist" — the applet ignored timedated's change signal and caught up at the
  next minute tick. `/usr/share/zoneinfo` now has real directories and
  `posix -> .` (Debian's layout), every edition. Checked on behoweather with a
  jiff test binary (fails) and in QEMU (finds every zone; the clock follows
  `timedatectl set-timezone` within 3 s; no parse errors).
- **Reboots hung for 90 s on cups-browsed** (behoweather: "stop-sigterm timed
  out", SIGKILL). Built without `--enable-saving-created-queues`, it deletes
  every network-printer queue it made while the system goes down.
  `KeepGeneratedQueuesOnShutdown Yes` in cups-browsed.conf, and its stop is
  capped at 10 s.
- **A sweep of behoweather's journal (2026-09-30) for things COSMIC expects but
  the image lacked** — all QEMU-tested on an installed system:
  - **Admin password prompts never worked.** cosmic-osd (COSMIC's
    authentication dialog) runs `/usr/libexec/polkit-agent-helper-1`;
    Buildroot's polkit puts it in `/usr/lib/polkit-1` ("failed to create
    helper", 9x). Linked. Behind that, polkit's own PAM file used Debian's
    `@include common-*` (rejected here, files absent): own
    `/etc/pam.d/polkit-1`. And `pkexec` refused users whose shell is
    `/usr/bin/zsh` because `/etc/shells` only listed `/bin/zsh`: both
    spellings now (every edition). Now: the dialog appears, the password
    authorises.
  - **No tray icons** (Steam, Discord, …): the status-area applet's
    StatusNotifierWatcher had no D-Bus activation file. Installed, plus the
    network applet's NetworkManager secret-agent policy (Wi-Fi passwords).
  - **The login screen had no wallpaper**: `cosmic-greeter-daemon` (reads
    each user's wallpaper/avatar/theme for the greeter) built at epoch-1.8.0,
    with its bus policy and a service.
  - **PipeWire ran without realtime priority**: users weren't in `pipewire`
    (installer adds it now) and `systemd --user`'s PAM stack had no
    `pam_limits`, so `@pipewire rtprio 95` never applied. Own
    `/etc/pam.d/systemd-user`. Now: PipeWire's data loop runs SCHED_FIFO.
  - **Build-machine paths in systemd units** (`dbus.socket` for user
    services, `systemd-ask-password-plymouth.service`): rewritten, and the
    build fails if any remain (every edition).
- **Right-clicking the desktop did nothing** (and "Show in folder" had no
  file manager behind it). cosmic-session starts `cosmic-files-applet` — the
  desktop layer (desktop icons, its right-click menu: new folder, open in
  terminal, change wallpaper, display settings) and the
  `org.freedesktop.FileManager1` service — but only `cosmic-files` had been
  built ("failed to start cosmic-files-applet: No such file or directory").
  Built at epoch-1.8.0 and installed. QEMU: the desktop menu opens.
- **Firefox had the generic gear icon in the dock** after its first-boot
  install. Flatpak creates its export folders with the first app, after the
  panel had scanned for icon folders; the folders now exist from boot
  (tmpfiles). QEMU: an app added mid-session gets its icon. (A reboot or
  panel restart also fixes an affected session.)
- **The lock screen had a plain grey background** (behoweather). Version skew:
  `cosmic-greeter` had been built at epoch-1.9.1, whose locker no longer draws
  the wallpaper itself but relies on cosmic-bg drawing it under the lock
  through the new `cosmic-session-lock-layer-v1` protocol (cosmic-comp #2792,
  cosmic-bg #144, both epoch-1.9.0), while cosmic-comp and cosmic-bg are
  epoch-1.8.0. Rebuilt cosmic-greeter at epoch-1.8.0, matching them. QEMU: the
  lock screen shows the wallpaper and unlocks. (The login screen's grey is a
  different gap: the greeter daemon, see TODO.)
- **COSMIC Player (any GStreamer/ALSA app) had no sound** while Firefox did
  (behoweather). PipeWire's ALSA config sat in `/usr/share/alsa/alsa.conf.d`,
  which alsa-lib doesn't read, so ALSA's default was the card's analog jack,
  not PipeWire's output (the monitor). Linked into `/etc/alsa/conf.d`.
  Confirmed on behoweather: the stream now reaches PipeWire's default sink.
- **COSMIC Files listed "pengyos-data" and failed to open it.** The new 8 GiB
  root-b partition lay over the previous install's data volume and kept its
  btrfs signature. The installer now wipes root-b, and a udev rule hides
  PengyOS's own partitions (ESP, root slots, data) from UDisks.
- **Dead keys and Compose did nothing** (accented characters): every COSMIC app
  logged `couldn't find a Compose file for locale "en_US.UTF-8"`. libX11 (now in
  the Desktop for Xwayland) ships the table, but Buildroot's locale purge
  deleted it and `compose.dir`; the Desktop post-build restores them from
  staging. QEMU: 79 errors per boot before, 0 after.
- **Desktop rootfs 3000M → 3500M**: 92% full once Xwayland landed.
- **Root slots 4 GiB → 8 GiB** (installer; minimum disk 18.25 GiB): the NVIDIA
  Desktop was at 3.1 GiB. Existing installs keep 4 GiB slots until reinstalled.
- **Downloads vanished; COSMIC Files' sidebar was bare** (behoweather,
  2026-09-30). Nothing created the user folders: Firefox (Flatpak, granted
  `xdg-download`) saved into a ~/Downloads that did not exist on the host, so
  the file lived only inside the sandbox and was gone. New package
  `xdg-user-dirs`, run as the user at every session start; COSMIC Files lists
  the folders once they exist.
- **cosmic-monitor drew its graphs in the wrong places.** The image had no
  Vulkan loader (radv was built but unusable) and no Intel Vulkan driver, so
  wgpu fell back to its GL backend, which drew only the last canvas of a window
  — reproduced in QEMU; iced's tiny-skia renderer draws the same window right.
  Desktop now ships the Vulkan loader, anv (Intel) and lavapipe (CPU fallback)
  next to radv; Mesa writes one ICD manifest per driver (it wrote
  `radeon_icd..json`: Buildroot's meson cross file has no CPU name).
- **No way to manage printers.** COSMIC Settings has no printer page upstream.
  Network printers already worked (cups-browsed added the Brother MFC-L3720CDW
  on its own; every print dialog lists it); a "Printers" launcher now opens the
  CUPS admin pages, and members of `wheel` administer CUPS. Scanning works at
  the SANE level (sane-airscan finds the MFC); a scanning app comes from the
  Store (Flatpak).
- **No network printers or scanners ever appeared.** avahi (mDNS/DNS-SD) was
  not in the image, so CUPS was built `--with-dnssd=no` and cups-browsed / SANE's
  escl backend could discover nothing; the firewall also dropped mDNS. Added
  avahi + `sane-airscan` (driverless eSCL/WSD scanners) and an mDNS allow rule;
  driverless (IPP Everywhere / AirPrint) printers now appear in print dialogs on
  their own. COSMIC 1.8 has no printer settings page — adding printers by hand is
  the CUPS web page for now (US-DSK-13).
- **Blank keyboard-layout button on the lock screen.** COSMIC's `xkb_config`
  defaults to an empty layout (upstream's first-run setup writes one; PengyOS
  never ran it), so the locker had no layout to show. The Desktop now ships a
  system default (`us`); a choice in Settings overrides it.
- **Software rendering was Mesa's slowest rasterizer.** Only softpipe was built;
  llvmpipe (LLVM is already in the image) is now the fallback when there is no
  GPU driver — e.g. an NVIDIA-only machine before the NVIDIA extension. (The
  "corrupted" output seen under QEMU `-vga std` is a QEMU artifact; COSMIC renders
  correctly on a pure firmware framebuffer, `-device ramfb`.)
- **Missing fonts: emoji as empty boxes, COSMIC in DejaVu.** The `fonts`
  package was an empty stub (an odd Config.in symbol, no recipe, never enabled),
  so the image had only DejaVu: emoji without a DejaVu glyph were boxes, the rest
  monochrome, and COSMIC's UI fell back from Fira. New packages `font-fira`
  (Fira Sans, every weight, + Fira Mono — libcosmic's defaults) and
  `font-noto-color-emoji` (Noto Color Emoji 2.051, the single font file);
  FreeType already renders its PNG (CBDT) glyphs. The stub is gone.
- **A 0% battery icon on desktops** (behoweather, an N100 desktop). DMI and
  systemd identify it correctly as a desktop; the "battery" is a Logitech
  wireless receiver's peripheral (`hidpp_battery_0`, visible since the HID++
  driver fix). COSMIC's battery applet (epoch-1.8's upower subscription) said
  "no battery" once, then took the next UPower change as a battery update. Patch
  in `cosmic-patches/cosmic-applets/` (backport of cosmic-settings a366fc7): after
  "no system battery", ignore updates. The profiles' "Battery" label is COSMIC's
  name for power-saver, not a detection issue.
- **Empty desktop on some boots (no panel/dock) — the splash handoff raced the
  compositor.** `plymouth quit --retain-splash` only asks plymouthd to quit; on
  the N100 cosmic-comp sometimes started before plymouthd let go of DRM master
  (seatd: "Could not make device fd drm master: Device or resource busy"), ran
  "unprivileged" and could not modeset: the session died and restarted 3 s later
  (installed disk) or came up without panel and dock (live stick). Two faults:
  the quit is asynchronous, and on an installed system the handoff commands ran
  as the desktop user (ExecStartPre runs as User=), whom plymouthd ignores — the
  session only ever came up after failing once. They now run as root (`+`) and
  the session waits for plymouthd to exit (`plymouth --wait`, bounded to 10 s).
  On behoweather: 3/3 clean boots, desktop at ~8 s instead of ~23 s.
- **cosmic-notifications gave the panel only 1 s to connect** (upstream, still
  in epoch-1.9; cf. pop-os/cosmic-panel#635): a panel slow to start lost the
  notifications applet. 30 s now — first patch under
  `board/qemu-x86_64-desktop/cosmic-patches/` (applied to the host-built COSMIC
  sources before building).
- **No lock screen.** COSMIC's lock screen is `cosmic-greeter` (in locker mode,
  started by cosmic-session); its host build had failed on Sep 25 (no PAM
  headers) so it was never in the image, and Super+L / idle / lid lock did
  nothing. Built at epoch-1.9.1 and installed, with a `cosmic-greeter` PAM
  service; and `unix_chkpwd` is setuid now (Server layer `permissions.txt`) —
  Buildroot installs it 0755, so pam_unix could never have checked an
  unprivileged user's password.
- **A systemd <-> polkit dependency cycle broke any rebuild of systemd** (and a
  clean Desktop build). `BR2_PACKAGE_SYSTEMD_POLKIT` makes systemd depend on
  polkit, while our polkit override (logind session tracking) makes polkit wait
  for systemd's staging install; make dropped an edge and ran systemd's staging
  install before its build. The option is off now and `external.mk` passes
  `-Dpolkit=enabled` to systemd directly (it needs polkit only at runtime).
- **A 90 s boot stall on the Desktop** once systemd was rebuilt with TPM2
  support (tpm2-tss is in the Desktop; the previous systemd had been built before
  it and never had TPM2): `systemd-tpm2-generator` waits for `/dev/tpmrm0`
  whenever it believes the firmware has a TPM, and on a VM or a board whose
  firmware reports one that never appears, it timed out after 90 s. Nothing needs
  the TPM in early boot, so the Desktop overlay masks the generator.
- **Fragment options that never reached the kernel.** The new checker found
  them: the DesignWare I2C drivers (sat under an unset `I2C_DESIGNWARE_CORE`),
  so no I2C touchpads; no pinctrl driver at all, which I2C-HID touchpads need for
  their GPIO interrupt (Sunrisepoint ... Meteor Lake, AMD added, built in); the
  Logitech Unifying receiver and PlayStation controller drivers (needed
  multicolor LEDs / `HID_LOGITECH`); `ATLANTIC` (now `AQTION`);
  `USB_HIDDEV=m` (a bool); `ITCO_VENDOR_SUPPORT` (gone from 7.2).
- **rtw88 Wi-Fi firmware missing from the Desktop and Core images** although
  enabled: linux-firmware had been built before the option reached those
  defconfigs, and Buildroot never rebuilds a package whose options change.
  `pengyos-build configure` now removes the build dir of any built package whose
  `BR2_PACKAGE_*` options changed (`tools/pengyos-build`, `stale.py`).
- **Screenshot dialog mode buttons had no icons** (todo #88): the portal ships
  `screenshot-{selection,window,screen}-symbolic` in `data/icons`, but our
  `cosmic-portal` recipe installed only the binary. It installs them into
  hicolor now.
- **Files opened in the wrong app — images went to the text editor.** Buildroot's
  `shared-mime-info` installs only glib's compiled `mime.cache` to the target,
  not the text DB the freedesktop spec (and the Rust `xdg-mime` crate) read:
  `globs`, `globs2`, `magic`, `aliases`, the per-type `subclasses` dirs, …
  cosmic-files identifies a file's type via `cosmic::mime_icon` →
  `xdg_mime::SharedMimeInfo`, which parses those text files — so with them
  missing it could identify *nothing* and fell back to a special mime that
  CosmicEdit handles. Hence double-clicking a PNG opened the text editor while
  "Open With → Swayimg" (a path that doesn't guess the type) worked. Fix:
  install the full compiled DB from staging (`external.mk` post-install hook on
  `shared-mime-info`). (XDG/`gio` associations were correct all along — they use
  the compiled cache.)
- **`flash-usb.sh` skipped the hybrid MBR — so the N100 wouldn't enumerate the
  stick.** The guard checked `sgdisk -i 1 | grep -q EF00`, but `sgdisk -i`
  prints the GUID *name* (`(EFI system partition)`), never `EF00`, so the check
  always failed and the hybrid-MBR step (the 2026-09-27 fix for picky firmware)
  never ran. Match the description instead. Caught while flashing the Desktop
  installer to the N100 stick (2026-09-29); applied `sgdisk -h 1` by hand that
  time.
- **No HDMI audio — the monitor's speakers were unreachable** ("line out"
  was the only output in Sound settings). The kernel and ALSA were fine
  (`card 0` exposed `HDMI 0 [Display]` + HDMI 1-3), but PipeWire/WirePlumber
  exposed only a single generic "Analog Stereo" profile: WirePlumber defaults
  every card to ACP (`api.alsa.use-acp=true`), which for this HDA card builds
  no HDMI profiles, and the **ALSA UCM data** (`/usr/share/alsa/ucm2`) that the
  UCM path needs was absent (Buildroot ships no `alsa-ucm-conf`). Added the
  from-source `alsa-ucm-conf` data recipe (`package/alsa-ucm-conf/`, ucm2 tree
  → `/usr/share/alsa/ucm2`, version-pinned 1.2.16) and a board-overlay
  WirePlumber rule (`51-alsa-ucm.conf`, `api.alsa.use-acp=false`, scoped to
  PCI/HDA cards via `alsa_card.pci-*` so non-PCI devices keep the upstream ACP
  default) that selects the UCM path. Verified on a clean **reinstall** of the
  Desktop on the N100: the Display/HDMI sinks appear, WirePlumber routes to the
  Display port (`hw:0,3`), and HDMI audio **plays through the monitor's
  speakers**. (The earlier silence was on the pre-reinstall, in-place box, not
  an image defect.)
- **Flatpak apps never appeared in Applications.** The COSMIC session unit
  hardcoded `XDG_DATA_DIRS=/usr/local/share:/usr/share`, dropping Flatpak's
  export dir — so a Store-installed app had its launcher on disk
  (`/var/lib/flatpak/exports/share/applications/<id>.desktop`) but the panel's
  app grid (freedesktop lookup via XDG_DATA_DIRS) never saw it. Added
  `/var/lib/flatpak/exports/share` to the session's `XDG_DATA_DIRS`
  (system-scope installs; a system unit can't use `%h` for a home dir — it
  expands against the manager, giving `/root`, not `User=`).
- **`flatpak run` died with "Failed to execute child process bwrap"** — no
  Flatpak app could start. The Desktop stages the host's flatpak CLI + system
  helper, but not the two helpers flatpak EXECs for every run, so the sandbox
  had nothing to launch: `error: Failed to execute child process "bwrap" (No
  such file or directory)`, and with bwrap present, `bwrap: execvp
  xdg-dbus-proxy` → "Failed to sync with dbus proxy". Both are stock Buildroot
  2026.08 meson recipes, now selected in the Desktop layer
  (`BR2_PACKAGE_BUBBLEWRAP=y` + `BR2_PACKAGE_XDG_DBUS_PROXY=y`; bubblewrap
  installs `/usr/bin/bwrap` setuid, so it works even where unprivileged user
  namespaces are off). Verified on the flashed image: hot-staging the two
  binaries from the host made `flatpak run --command=sh …` reach `SANDBOX_OK`
  as the desktop user.
- **COSMIC Store could not install anything** ("Unit flatpak-system-helper.service
  not found"). Since the desktop runs as the installed user, system-wide
  installs go through flatpak's system helper, which D-Bus activates via
  systemd — and the image (flatpak is copied from the host) had the helper but
  not its unit. Shipped the unit (with the CLI's /usr/lib/compat libraries) and
  a polkit rule letting wheel install/update/remove apps without a password
  from their own session, as on Ubuntu/Fedora. Verified: Loupe installed from
  Flathub as the user. flatpak's install triggers (+ update-desktop-database)
  now ship too, so Store apps appear in "Open with".
- **No app opened images.** COSMIC has no image viewer: added swayimg (our
  package; native Wayland; PNG/JPEG/WebP/GIF/TIFF/BMP) and a system
  `mimeapps.list` (images → swayimg, text → COSMIC Edit, media → COSMIC Player,
  folders → COSMIC Files) plus the MIME index (`mimeinfo.cache`).
- **Installed Servers logged root in on the console.** tty1 and the serial
  console now auto-log-in only on live boots (the stick, a flashed image); an
  installed system (kernel line has `pengyos.data=`) asks for a login.
  `audit=0` on the kernel line: with PAM every login made an audit record the
  kernel printed over the console (nothing reads them — no auditd).
- **zsh's first-run wizard** greeted a new user's first terminal (and zsh's
  prompt showed `#`: logind gives every session CAP_WAKE_ALARM, which zsh's
  `%#` counts as privileged). New accounts get a starting `~/.zshrc` (history,
  completion, `~/.local/bin` on PATH, a `%`/`#` prompt by UID).
- **`pengyos-install image` over SSH on the Desktop stick** could run before the
  GUI had mounted the payload ("no install payload found"): the engine mounts
  the stick's PENGYOS-PAYLOAD partition itself when needed.
- **Core's tool stubs replaced real tools.** Core ships "not installed, use X
  instead" stubs, from its overlay. Overlays land after the packages and the
  rootfs merges all bin directories, so a stub overwrote the real binary of
  any tool the image did ship (Core's own `wipefs`; stacked, it would have hit
  Desktop's `sgdisk`, which the installer needs). Stubs are now installed by
  Core's post-build only where the tool is really missing.
- **Desktop GPU drivers built in:** amdgpu, radeon and nouveau were `=y` in the
  Desktop kernel fragment, so they probed before the rootfs and could never
  load their firmware (the i915 trap) — a black screen on real AMD/NVIDIA
  hardware. Now modules, like i915.
- **polkit never saw a logind session.** Buildroot's polkit recipe hardcodes
  `-Dsession_tracking=ConsoleKit`, so every user counted as outside an active
  local session: NetworkManager, Settings and Flatpak asked for admin auth (or
  refused) for the person at the desktop. `buildroot-external/external.mk` now
  overrides it to `logind` when systemd-logind is enabled.
- **Account merge kept the user in their groups.** The preinit account merge
  (first cut) took system groups wholesale from the image, which would have
  silently dropped the user from `wheel`/`seat`/`video` on every boot; it now
  merges by name, unions group members, and drops only stale system accounts
  (id < 1000) an older image had.
- **Shared SSH host keys (security).** The Desktop and Server images shipped SSH
  host keys generated at build time, so every machine installed from an image
  (and every stick) had the same host keys — anyone with the image could
  impersonate any PengyOS host. Keys are now generated per machine on first boot
  (`sshd-keygen.service`, before sshd); `post-build.sh` removes any from the
  image, and the installer deletes any it finds on the target. Hosts installed
  before this fix should run `rm /etc/ssh/ssh_host_* && ssh-keygen -A` and
  restart sshd (clients will see a changed host key, as they should).
- **Installer front-ends never installed.** The GUI listed no disks (hardcoded
  empty), had no Continue/Back past the Disk screen, reset the password field on
  every keystroke, and its "install" only wrote an `install.toml` (without the
  password); the TUI's `run` echoed its answers and exited. Both now drive the
  engine (see above). `pengyos-install` no longer declares console scripts named
  `install` / `plan` / `provision-plan` (a pip install shadowed coreutils'
  `install`). The engine's A/B `plan`/`install` path is unchanged and still not
  executable against a real disk — see its README.
- **Desktop: Light mode and the Appearance previews.** `cargo install` skips
  cosmic-settings' data, so the image had no system `CosmicTheme.*` defaults
  (choosing Light re-themed nothing) and no illustration icons (the Dark/Light
  buttons were empty). The hand-written hicolor `index.theme` also had no
  per-directory sections, so no hicolor-only icon resolved; the real
  `hicolor-icon-theme` package replaces it. An install where Light was already
  clicked must drop `~/.config/cosmic/com.system76.CosmicTheme.*` once.
- **Desktop: Restart / Power Off did nothing.** cosmic-osd calls
  `org.freedesktop.login1`; the image now builds `systemd-logind` (+ polkit
  support) alongside seatd.
- **Desktop: launcher and keyboard shortcuts were dead.** `pop-launcher` (the
  search backend behind cosmic-launcher) is now built and installed, and
  cosmic-comp's keybindings + cosmic-settings-daemon's `system_actions` are
  installed.
- **Desktop: 2-minute boot stall.** Buildroot's default `systemd-networkd` ran
  beside NetworkManager and `systemd-networkd-wait-online` timed out every boot;
  networkd is no longer built.
- Desktop polish: the session starts in `$HOME` (terminals opened in `/`); the
  `cosmic.desktop` session file no longer appears as a "COSMIC" app; the
  Workspaces stub is hidden and has its own dock icon; the restart dialog icon
  (`system-restart-symbolic`) resolves.
- Server image: `sshd` crash-loop (`Address already in use`) and the missing host
  keys (`no hostkeys available -- exiting.`) — see the SSH entry above.

## [0.1.0] — 2026-09-17 — "boot to a console" (the M1 milestone)

The first milestone: **one reproducible, human-watched source → boot pipeline,
end to end, on physical hardware.** Definition of done — all three met:

1. **Build from pinned sources** — `pengyos-build build pengyos-core-m2` →
   `bzImage` + `rootfs.ext2` + `pengyos-m2-x86_64.img` (a bootable GRUB/genimage
   GPT disk), from committed sources, glibc, pinned **Buildroot 2026.08**. `rc=0`.
2. **Boot headless** — `pengyos-qemu-verify boot … --uefi` reaches
   `pengyos-m2 login:` (systemd): `reached-login` PASS, `no-kernel-panic` PASS.
3. **Real hardware** — the same image boots to a console on a physical machine,
   an **Intel N100 mini-PC ("behoweather")**.

_Real-hardware boot facts (N100, 16 GB, UEFI, ethernet):_ console on the monitor
(`fbcon: i915drmfb (fb0) is primary`), auto-login root; NIC up on **DHCP**
(`10.0.23.4`) with sshd reachable on `0.0.0.0:22`; `systemd` PID 1,
`is-system-running = running`, no failed units; **idle RAM ≈ 224 MB of 16 GB**
(the US-CORE-1 acceptance criterion).

_Re-verified 2026-09-21:_ the rebuilt Core image (1 GiB rootfs + installer/rescue
kit + `python3`, sha256 `7a45abc6…`) was written to USB and booted on the same
N100 — same console/login, DHCP and key-only sshd, now with the full-screen GRUB
menu and the 16×32 console font. Flashing required the now-mandatory post-`dd`
`sgdisk -e` GPT repair (see
[`scripts/flash-usb.sh`](scripts/flash-usb.sh)).

### Added
- **PengyOS Core** — the shared minimal base (systemd userspace: glibc, D-Bus,
  `systemd-networkd`/`resolved`, chrony, journald, udev), built from pinned
  sources with Buildroot 2026.08 (Linux 7.2.5).
- **PengyOS Core M1** — the earlier minimal busybox console target, kept as the
  smallest reference build.
- **Bootable image pipeline** — `pengyos-build` produces a GPT/EFI disk image
  (GRUB2 + kernel + ext4 root); `pengyos-qemu-verify` boots it headless in
  QEMU/KVM and asserts readiness.
- **Real-hardware boot** — see the facts above.
- **Provenance groundwork** — a signed SBOM embedded in the image
  (`pengyos-sbom`), an append-only attestation log (`pengyos-attest`), and a
  pinned component manifest (`pengyos-pins`).
- **The toolkit** — the first waves of the Ralph-built tool set under
  [`tools/`](tools/) (build/verify harness, provenance, curation, install/update,
  Desktop/COSMIC packaging).

### Fixed — the M1→M2 integration findings (2026-09-16 → 09-17)

Ten issues found while turning the first builds into a bootable, reachable Core
node. Kept here as the build's own history:

1. **`external_tree` path** — resolved by putting the canonical `targets.toml` at
   the repo root, next to `buildroot-external/`.
2. **Host `install` was uutils coreutils** — worked around with a
   `~/.local/bin/install → gnuinstall` shim; the permanent fix is a root repoint.
3. **Silent glibc → uClibc fallback** — pinned Buildroot **2026.08** (kernel
   headers 7.1 ≤ kernel 7.2.5) so the build selects glibc, not uClibc.
4. **Upstream mirror flakiness** — mitigated by priming from
   `sources.buildroot.net`; a pinned local mirror remains a worthwhile TODO.
5. **Stale manual build state** — the tool builds under
   `~/build/pengyos/output/<target>/`; the old 2026-09-12 hand-built tree is
   separate and must not be confused with it.
6. **M2 wasn't buildable** — folded into the single `BR2_EXTERNAL` tree +
   `targets.toml`.
7. **No remote access, no NIC config** — added `sshd` (key-only, with build-time
   public-key injection) and `systemd-networkd` DHCP.
8. **No provenance** — `pengyos-build sbom` (legal-info + show-info → SPDX /
   CycloneDX, signed) and embedded into the image.
9. **Blank screen on real hardware** — the kernel had no framebuffer console;
   added simpledrm + fbcon (crucially `CONFIG_SYSFB_SIMPLEFB`) and a
   `getty@tty1`.
10. **Shipped firmware never loaded** — drivers were built-in and probed before
    the rootfs was mounted; made `i915` / `cfg80211` / `iwlwifi` **modules** and
    shipped a `linux-firmware` subset. i915 now loads DMC/GuC/HuC cleanly.

> HTTP references for this milestone: agent-memory posts
> `8480c912b587421f1d010000` (real-hardware boot), `48550b6a1895432c12010000`
> (M2 built), `e7de77772c5691f418010000` (SBOM),
> `44e943872f4b187016010000` (US-CORE gap analysis). Commits: `ed6d4ab`,
> `55616cc`, `62296cf`, `d75949e`, `ce873c7`.

