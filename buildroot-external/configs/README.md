# `buildroot-external/configs/` — the target defconfigs

Each `*_defconfig` here is a Buildroot "defconfig": the complete declarative
description of **one buildable target** — which packages to build, which kernel
fragment, which board, which filesystem and image layout. A build is
`pengyos-build build <target>` (or, directly, `make BR2_EXTERNAL=… <name>_defconfig
&& make`). The repo-root [`../../targets.toml`](../../targets.toml) maps a target
name to one of these files.

The editions are **layered** — Core ⊆ Server ⊆ Desktop. Every defconfig is
**generated**: each layer lives once in [`layers/`](layers/README.md)
and `scripts/compose-defconfigs.py` stacks them per `layers/editions.toml`.
**Edit the layer, never a generated defconfig**, then run
`scripts/compose-defconfigs.py` (`--check` fails when one is stale; a test runs
it).

## The targets

| Defconfig | Edition / milestone | Notes |
|---|---|---|
| `pengyos_core_defconfig` | **Core** | The systemd base — the current "Core". Boots on real N100 hardware. |
| `pengyos_server_defconfig` | **Server** | Core + the comfort-CLI set, rootless podman, monitoring, backup. |
| `pengyos_desktop_defconfig` | **Desktop** | Server/Core base + Wayland + COSMIC + graphics/audio/network. The main Desktop image. |
| `pengyos_desktop_nvidia_defconfig` | **Desktop (NVIDIA variant)** | **Opt-in**, not built by default. The Desktop base plus the proprietary NVIDIA driver on the open kernel modules (`package/nvidia-open`, RTX 20 and newer; a documented Tier-2 concession — see `SPEC.md` §2.5 and `tools/pengyos-desktop-nvidia`). |

## Conventions

- **One layer, one board.** Each layer names its own board's overlay, kernel
  fragment and post-build script; those three lists accumulate across layers,
  so a Desktop build uses the core, server and desktop boards in that order. The
  post-image script (and so `genimage.cfg`) is the edition's own board's.
- **No hallucinated symbols.** Every `BR2_PACKAGE_*` must resolve against the
  pinned Buildroot 2026.08 tree or one of our authored recipes. Each defconfig
  tool ships a linter that *resolves* the config and fails on a dropped or
  unknown symbol.
- **The proprietary driver never leaks into the defaults.** `BR2_PACKAGE_NVIDIA_OPEN*`
  appears only in the `nvidia` layer (so only in `pengyos_desktop_nvidia_defconfig`); `pengyos-desktop-nvidia lint`
  asserts it is absent from every other defconfig (and that Buildroot's legacy
  390 `nvidia-driver` is set nowhere).
