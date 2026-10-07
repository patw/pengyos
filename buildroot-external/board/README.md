# `buildroot-external/board/` — per-board build glue

A **board** in Buildroot is everything around the packages: how the disk image
is laid out, which bootloader is installed, what kernel options a machine needs,
and which files are overlaid onto the root filesystem. Each subdirectory here is
one board. Since the defconfigs became layered (see
[`../configs/layers/README.md`](../configs/layers/README.md)), each board is the
glue for **one layer**, and an edition stacks the boards of its layers: a
Desktop build applies the core, server and desktop overlays, kernel fragments and
post-build scripts, in that order. Only the post-image script (and so the disk
image layout) is the edition's own board's.

Boards are named after their layer (`core`, `server`, `desktop`). Every image is
x86_64 UEFI: it is boot-tested under QEMU (q35 + OVMF) and runs on real
hardware (Intel N100 mini-PCs, Intel and AMD laptops).

## Boards

| Board | Layer (used by) | What it is |
|---|---|---|
| `core/` | `core` (every edition) | **Core** — the systemd userspace base: glibc, D-Bus, networkd/resolved, chrony, journald, udev, key-only sshd, signed SBOM, video console. Its `post-build.sh` runs first in every edition; `tool-stubs/` holds the "not installed" stubs. |
| `server/` | `server` (Server, Server NVIDIA, Desktop) | **Server** — remote-work + rootless containers + monitoring/backup + nftables, and the shared install/update machinery (preinit, the A/B layout, `pengyos-update`). |
| `desktop/` | `desktop` (Desktop, Desktop NVIDIA) | **Desktop** — the COSMIC session + graphics/audio/network stack. The NVIDIA editions add no board: their driver files come from `package/nvidia-open` (and `nvidia-container-toolkit`), and each post-build names the image `… NVIDIA` when `nvidia-smi` is in it. |
| `common/` | — | Shared by every edition: `os-release.sh` (from the root `VERSION`), `installer-image.sh` (the install payload + the installer stick for Server and Desktop), `image-ids.sh` (disk and filesystem IDs derived from os-release, for reproducible images), `check-needed-libs.py` (the end of each post-build: fail if an ELF needs a library the image lacks) and `pyc-compile.py` (per-file Python byte-compiling, called from `external.mk`). |

## What each board contains

```
board/<name>/
├── genimage.cfg        # the disk image recipe: GPT + EFI System Partition + rootfs
├── grub-efi.cfg        # the GRUB menu entry (kernel cmdline, root= by PARTUUID)
├── grub-pengyos.pf2    # the PengyOS GRUB theme font (Core/Server/Desktop)
├── linux.fragment      # kernel config fragment: this board's drivers/options
├── post-build.sh       # runs after the rootfs is assembled (branding, ssh keys, SBOM, …)
├── post-image.sh       # runs after the image is generated (GRUB staging, genimage)
└── rootfs-overlay/     # files copied verbatim onto the target root (a mirror of /)
```

- **`linux.fragment`** is merged onto the base x86_64 kernel defconfig — this is
  how a board gets exactly the drivers it needs and nothing more (see
  `tools/pengyos-kernel-fragments` and `pengyos-hwprofile-kernel`).
- **`rootfs-overlay/`** mirrors the target `/`. It carries our branding
  (`/etc/motd`), systemd units and drop-ins,
  `/etc/skel` defaults, and service config (NetworkManager, sshd, nftables).
- **`post-build.sh`** is where image-time facts are baked in: the hostname,
  os-release and `/etc/issue`, session defaults, optional `/root/.ssh/authorized_keys` (via
  `PENGYOS_AUTHORIZED_KEYS_FILE`), and the embedded SBOM. **SSH host keys are
  never baked in**: the core script deletes them and each machine generates its
  own on first boot. `post-image.sh` assembles the bootable image with `genimage`.
- **The Desktop's COSMIC binaries are host-built.** The COSMIC compositor,
  shell and apps, `pop-launcher`, `pengyos-install-gui` and `pengyos-updates`
  are built with cargo on the build host by `scripts/build-cosmic.sh`
  (`~/cosmic-src/<component>` → `~/cosmic-build/release`, and
  `~/cosmic-build/install-gui/release` for the two PengyOS apps) and copied in, then stripped, by
  `desktop/post-build.sh`. Set `PENGYOS_COSMIC_SRC` /
  `PENGYOS_COSMIC_BUILD` to use other directories. Each copy is guarded: a host without
  them still builds, but the image has no COSMIC session, installer GUI or
  update app. Patches we carry on COSMIC live in
  [`desktop/cosmic-patches/`](desktop/cosmic-patches/README.md).
