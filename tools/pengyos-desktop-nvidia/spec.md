# PengyOS desktop-nvidia — containment lint for the opt-in NVIDIA editions

## Summary
Prove that the proprietary NVIDIA driver (the in-tree `nvidia-open` package) is
contained to the opt-in `pengyos_server_nvidia_defconfig` and
`pengyos_desktop_nvidia_defconfig`: set there, absent from every other defconfig,
declared and version-pinned, their build targets registered,
and — against a Buildroot checkout — surviving kconfig resolution.

## Purpose
SPEC §2.5: NVIDIA machines get the proprietary driver from opt-in, Tier-2
editions (Server NVIDIA for compute, Desktop NVIDIA for graphics too); the
default Desktop, Core and Server stay blob-free. The editions themselves are
data (the `nvidia-compute` and `nvidia` defconfig layers + the `nvidia-open`
recipe); this tool is the check that keeps them contained. (The name predates
the Server NVIDIA edition, 2026-10-03.)

## History (2026-09-29 redesign)
The first version (0.1.x) generated the edition around Buildroot's
`nvidia-driver` package: an `emit`/`wire` pair wrote a variant defconfig and
modprobe/udev/PRIME overlay files, a `record` wrote a concession JSON, and a
modular-Xorg "posture" was proven necessary because that package gates its
userspace behind the X.org driver. That package turned out to be the legacy 390
branch — no Turing+ support, no GBM, does not build on kernel 7.2 — so the
edition could never have driven the fleet's RTX 30/50 GPUs or a Wayland session.
The driver moved to `package/nvidia-open` (open kernel modules + matching
userspace from one `.run`, with its modprobe/udev/suspend files), the edition is
composed from layers, and this tool was cut down to its lint.

## Interface
- `pengyos-desktop-nvidia lint --repo BR2_EXTERNAL_DIR [--buildroot DIR] [--targets FILE] [--json]`

## Verification (tests are first-class)
- Each rule passes on a well-formed fixture tree and fires — alone — on its
  mutation (a variant's required symbol missing — the driver for both,
  graphics and libglvnd for the Desktop; a driver symbol, incl.
  a sub-option, in a default; the legacy driver in any defconfig; the package
  not sourced / not declared; a version bump without a hash; either target
  missing; a kconfig silent drop in either variant; a board `modprobe.d`
  file that stubs out or blacklists an `nvidia*` module).
- `lint` passes against the real `buildroot-external` tree.
- The kconfig resolve runs `make -C <buildroot> O=<tmp> BR2_EXTERNAL=<repo>
  <variant>` for each NVIDIA defconfig and writes nothing into the checkout or any
  real output dir; `make` is injectable for the hermetic suite.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Non-goals
- Building or booting the NVIDIA images (operator/build work).
- Secure Boot signing of the out-of-tree `nvidia*` modules — a follow-up with
  the boot-chain work.
- Making the NVIDIA edition reproducible/SBOM-clean (impossible with a blob;
  recorded as an exception in SPEC §2.4/§2.5).

## Constraints
- Runs as a non-root user with no network at test time; standard library only.
