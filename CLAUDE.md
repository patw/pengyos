# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Warning

Never auto commit to this repo, the user will always do that themselves.  Also the machine is tight on memory use /data/tmp not /tmp for temporary stuff.

## What this is

PengyOS is a from-source Linux distribution built with **Buildroot 2026.08**, in three nested editions: **Core ⊆ Server ⊆ Desktop** (plus opt-in Server NVIDIA and Desktop NVIDIA variants). The Desktop runs COSMIC behind greetd. Installed machines use an A/B layout (two read-only ext4 root slots chosen by GRUB, plus a btrfs `data` partition) and update from a signed feed. `README.md` is the user-facing front page (editions, install, what isn't ready); `docs/principles.md` holds the *why* (principles, the maintenance loop, the component rubric, the failure modes); `SPEC.md` the *what* (component decisions and rejected alternatives); `USE-CASES.md` the user stories. When `USE-CASES.md` and the spec disagree, `USE-CASES.md` wins.

## Commands

```sh
# Unit tests for one tool (each tools/<id>/ is its own Python or Rust project)
cd tools/<id> && python3 -m pytest -q          # or: cargo test
python3 -m pytest -q tests/test_foo.py::test_bar   # a single test

# Repo-level integration gate. A tool's own suite can pass while the wiring is broken.
python3 scripts/pengyos_gate.py run --repo . --mode fast   # wiring lints, offline
python3 scripts/pengyos_gate.py run --repo . --mode full   # + every tool's verify.toml (Python and Rust) + recipe source checks
#   --base REF limits checks to what changed since REF

# Defconfigs are GENERATED. Edit the layer, then recompose.
scripts/compose-defconfigs.py            # rewrite configs/pengyos_*_defconfig
scripts/compose-defconfigs.py --check    # exit 1 if stale (a test runs this)

# Build an edition (targets come from the repo-root targets.toml; output goes to ~/build/pengyos/output/<target>/)
cd tools/pengyos-build
python3 -m pengyos_build list  --targets ../../targets.toml
python3 -m pengyos_build build pengyos-desktop --targets ../../targets.toml

# Boot headless under QEMU/KVM + UEFI and assert it reaches a login prompt
cd tools/pengyos-qemu-verify
python3 -m pengyos_qemu_verify boot ~/build/pengyos/output/<target>/images/<img> --uefi
```

Targets: `pengyos-core` (Core), `pengyos-server`, `pengyos-server-nvidia`, `pengyos-desktop` (the main Desktop), `pengyos-desktop-nvidia`.

Host gotcha: a trailing colon in `LD_LIBRARY_PATH` breaks Buildroot. `pengyos-build build` drops the empty and `.` entries Buildroot rejects, but run raw `make` or other host tools with `env -u LD_LIBRARY_PATH`.

`docs/qemu-testing.md` is the runbook for booting, SSH (`PENGYOS_AUTHORIZED_KEYS_FILE` at build time, then `--hostfwd tcp::2222-:22`), serial console, testing the installer, and screenshotting the Desktop via Xvfb + virgl. If you change an image or a board, build it and boot it; the unit tests alone are not enough.

## Architecture

### Buildroot external tree (`buildroot-external/`)
- **Defconfig layers.** `configs/layers/{core,server,desktop,nvidia-compute,nvidia}.config` are stacked per `configs/layers/editions.toml` to produce `configs/pengyos_*_defconfig`. Never hand-edit a generated defconfig. A symbol set in a later layer overrides the earlier one. Three lists *accumulate* in layer order instead: `BR2_ROOTFS_OVERLAY`, `BR2_ROOTFS_POST_BUILD_SCRIPT`, `BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES`. Put a package or overlay file in the **lowest** layer that needs it.
- **Boards map to layers.** `board/core` is the core layer, `board/server` the server layer, and `board/desktop` the desktop layer (each NVIDIA edition reuses its base edition's board; `nvidia-compute` and `nvidia` have no board). Each layer has a `rootfs-overlay/`, `post-build.sh`, and `linux.fragment`. The core `post-build.sh` runs first in every edition: SSH key injection, provenance, the SBOM, and the tool stubs. Post-image scripts are per edition. Shared installer-stick and os-release logic lives in `board/common/`.
- **Overlay rules.** Overlays land after packages and `/bin`, `/sbin`, `/usr/sbin` merge into `/usr/bin`. So never put a tool stub in an overlay; stubs go in `board/core/tool-stubs/`. An overlay `/etc/group` *replaces* the skeleton's copy, so it must keep `root:` (the gate checks this).
- **Authored recipes** live in `package/<name>/`. They are auto-included by `external.mk` but must also be `source`d in `Config.in`. Every `BR2_PACKAGE_*` symbol must resolve against Buildroot 2026.08 or one of these recipes. `external.mk` also holds overrides of stock Buildroot recipes (polkit/logind, gvfs MTP, PipeWire codecs, shared-mime-info, …), each with a comment saying why. Follow that pattern for new overrides.
- **COSMIC is host-built, not a Buildroot recipe (yet).** The COSMIC compositor, shell and apps, `pop-launcher`, and the PengyOS libcosmic apps (`pengyos-install-gui`, `pengyos-updates`, `pengyos-disks`, `pengyos-usage`, `pengyos-gamepads`, `pengyos-printers`, `pengyos-firmware`: the `for app in` list in `build-cosmic.sh`) are built with cargo on the build host by `scripts/build-cosmic.sh` (pinned commits in `scripts/cosmic-components.tsv`; `$PENGYOS_COSMIC_SRC/<component>` → `$PENGYOS_COSMIC_BUILD/release`, defaulting to `~/cosmic-src` and `~/cosmic-build`; the PengyOS apps go to `$PENGYOS_COSMIC_BUILD/install-gui`). Post-build also copies a few host libraries those binaries link, so a Desktop build needs an Ubuntu 26.04 host provisioned with `scripts/provision-build-host.sh --desktop`. Flatpak is a recipe (`package/flatpak`). `board/desktop/post-build.sh` copies them in, and each copy is guarded, so a host without them still builds an image that is silently missing them. Patches we carry on COSMIC live in `board/desktop/cosmic-patches/`. After changing one of these Rust apps, rebuild it (`scripts/build-cosmic.sh pengyos-updates`) before rebuilding the image. **`cosmic-files` and `cosmic-files-applet` are one cargo workspace** and the applet depends on the app crate with `desktop-applet`; they must be built as two separate cargo invocations (the TSV does), because a single `--workspace` build feature-unifies `desktop-applet` onto the app binary and the file manager then draws a second, interactive copy of its view on the desktop. `build-cosmic.sh` fails the build if the app binary carries the feature.
- **NVIDIA containment.** `BR2_PACKAGE_NVIDIA_OPEN*` may appear only in the `nvidia-compute`/`nvidia` layers and the two NVIDIA defconfigs. `pengyos-desktop-nvidia lint` enforces this.
- Buildable targets live in the repo-root `targets.toml` (the only one; `pengyos-desktop-nvidia lint` reads it too).

### `tools/` — one bounded project per directory
Each tool follows a fixed contract: `README.md`, `spec.md` (the contract with acceptance criteria), `LICENSE` (MIT), `CHANGELOG.md`, a `verify.toml` (how the gate runs its tests), and real tests. For a new tool, write `spec.md` first. Tools keep their own semver; only images use the root `VERSION`.

Key runtime components shipped in images (as opposed to build-time tooling):
- `pengyos-install`, `pengyos-install-gui` (libcosmic), `pengyos-install-tui`: the installer.
- `pengyos-update`: the on-device A/B updater (`apply` / `rollback` / `status`).
- `pengyos-updates`: the Rust Desktop app and notifier.
- `pengyos-disks`: *Disks*, the Rust Desktop app that partitions, formats and mounts any drive, writes images to and ejects removable ones (over UDisks2). It locks the running system's own partitions.
- `pengyos-usage`: *Disk Usage*, the Rust Desktop app that shows what takes the space as a ring chart (read-only).
- `pengyos-gamepads`, `pengyos-printers`, `pengyos-firmware`: the *Gamepads*, *Printers* and *Firmware* (fwupd) Desktop apps.

Build and release tooling: `pengyos-build`, `pengyos-qemu-verify`, `pengyos-release` (`update-publish`, `download-publish`), `pengyos-sbom`. COSMIC packaging: `pengyos-cosmic-pkg`, `pengyos-cosmic-recipes`, `pengyos-cosmic-native-deps`.

### Install and update flow
See `docs/state-layout.md`. `pengyos-release update-publish` writes `update/<IMAGE_ID>/<YY.MM.N>/` (a manifest signed with ed25519, `rootfs.ext4.zst`, `bzImage`). On a machine, `pengyos-update apply`:
1. writes the rootfs into the other slot;
2. runs the **new image's** `/usr/lib/pengyos/update.d/*` steps;
3. sets `pengyos_try` so the next boot tries the new slot once;
4. `pengyos-update-confirm.service` keeps the new slot only if it reaches multi-user.

Updates never touch `data`. Update feeds match on `IMAGE_ID`. The UKI / systemd-boot / systemd-sysupdate design in `docs/install-update-architecture.md` is **parked**. Its tools (`pengyos-sysupdate`, `pengyos-bootchain`, `pengyos-uki`, `pengyos-bootcount`) were retired and live in git history; `pengyos-install plan/install` are still in the tree but unused by any image.

## Conventions

- **Versioning.** Images use `YY.MM.N` from the root `VERSION` file. Bump it when publishing an image; a new month resets N to 0. `board/common/os-release.sh` writes `VERSION_ID`, `IMAGE_VERSION`, `IMAGE_ID`, and `BUILD_ID` (the git commit) from it.
- **Docs in the same change.** System-level changes get an entry in the root `CHANGELOG.md` (Keep a Changelog, under `[Unreleased]` or the image version). Tool changes go in that tool's `CHANGELOG.md`. Each directory's `README.md` must stay accurate. Open work lives as GitHub issues, not in a backlog file.
- **Small, atomic commits** whose messages say *why*.
- **Security boundary.** Release signing, update acceptance and privilege changes need a human's sign-off. Signing keys never live in this repo.
- **Licenses.** Prefer MIT/Apache-2.0 components. Any GPL/LGPL component or binary blob must be documented explicitly in `SPEC.md`.
