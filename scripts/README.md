# `scripts/` — host-side helpers

This directory contains the **shell and Python helpers that operate on the
project from the build host**. Nothing here is part of the shipped OS; it is
the tooling that *builds and drives* it.

> Building and boot-testing images is done by the `tools/pengyos-build` and
> `tools/pengyos-qemu-verify` CLIs; the scripts here cover the rest.

## Host-side how-tos to build/boot/flash

| Script | What it does |
|---|---|
| `run-qemu-disk.sh` | Interactive launcher: boot the newest image `pengyos-build` produced (`~/build/pengyos/output/*/images/`, or a directory/image you give it) under QEMU + OVMF, with a picker when several are present. Non-destructive (`snapshot=on`). |
| `flash-usb.sh` | Write a disk image to a USB stick / block device, then run the **mandatory** post-`dd` GPT repair (`sgdisk -e`, hybrid MBR). Bakes in the N100 enumeration fix (see the header comment). |
| `provision-build-host.sh` | Idempotently install a build host's prerequisites with apt (Buildroot host deps, QEMU/OVMF/swtpm). `--desktop` adds what the Desktop takes from the host: COSMIC's build dependencies, rustup, and the packages `post-build.sh` copies files from. Never runs `apt upgrade`. |
| `release.sh` | Rebuild, check and optionally publish: per target acquire → configure → build → SBOM (generated + embedded) → QEMU boot check (smoke matrix for Core/Server; for the Desktops a login plus `pengyos-qemu-verify desktop`); `--publish` signs into the local site dir (`$PENGYOS_RELEASE_KEY`), `--push` rsyncs it to `$PENGYOS_SITE_REMOTE`. Refuses a dirty tree when publishing. |
| `build-cosmic.sh` | Clone the COSMIC components pinned in `cosmic-components.tsv`, apply `board/desktop/cosmic-patches/`, and cargo-build them plus our two libcosmic apps into `$PENGYOS_COSMIC_BUILD`. Run before building the Desktop. It also fails the build if the `cosmic-files` **app** binary carries the applet's `desktop-applet` code (see the `cosmic-files` row). |
| `cosmic-components.tsv` | The COSMIC sources the Desktop is built from: repository, exact commit, cargo arguments. The arguments column may be a `&&`-joined command list when a component needs more than one cargo invocation — `cosmic-files` builds the app and the applet separately so the app binary never inherits the applet's `desktop-applet` feature (otherwise the file manager draws a second copy of its view on the desktop). |
| `check-kernel-fragments.py` | Replay Buildroot's kernel-config flow (base defconfig + each edition's fragments + `olddefconfig`) against a kernel source tree and list every fragment line Kconfig silently dropped (unmet dependency, renamed symbol). `--kernel-src DIR [defconfig ...]`. `--built KCONFIG --br-config BR2_CONFIG` checks an already-resolved kernel `.config` (fragment lines plus each layer's `kernel-required.txt`); the core post-build runs it and fails the build on a drop. |
| `check-daemon-features.py` | The userspace twin of the kernel guard: checks `board/desktop/features-required.txt` against an assembled rootfs and exits 1 when a daemon capability the edition claims is missing (the `--disable-hog` class of bug: a gamepad pairs and connects, and no input device ever appears). Run from the **desktop** post-build, which fails the build on a miss. `--target-dir DIR [--require FILE]`. |
| `compose-defconfigs.py` | Generate the edition defconfigs from `buildroot-external/configs/layers/` (Core ⊆ Server ⊆ Desktop, plus the NVIDIA layers). Edit a layer, then run it; `--check` exits 1 when a generated defconfig is stale. See [`../buildroot-external/configs/layers/README.md`](../buildroot-external/configs/layers/README.md). |
| `pengyos_gate.py` | The **repo-level integration gate**. A tool's own suite is self-consistent and can be green while the integration is broken, so the gate runs an *external* check over the tree (wiring lints, overlay guards, authored-recipe resolution). `run --repo . --mode fast\|full [--base REF]`. See its module docstring. |
