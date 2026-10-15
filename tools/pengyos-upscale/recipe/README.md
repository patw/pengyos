# Recipe skeleton — how this lands in `buildroot-external/package/`

These are **not installed**. They live here while the component is a prototype.
To enable it:

```sh
# 1. the recipe
cp -r tools/pengyos-upscale/recipe/realesrgan-ncnn-vulkan buildroot-external/package/
# 2. make the symbol visible
#    buildroot-external/Config.in, inside menu "PengyOS (external tree)":
source "$BR2_EXTERNAL_PENGYOS_PATH/package/realesrgan-ncnn-vulkan/Config.in"
# 3. turn it on for the Desktop (configs/layers/desktop.config)
BR2_PACKAGE_REALESRGAN_NCNN_VULKAN=y
BR2_PACKAGE_REALESRGAN_NCNN_VULKAN_MODEL_FAST=y      # 3.7 MB, the GPU path
# BR2_PACKAGE_REALESRGAN_NCNN_VULKAN_MODEL_PHOTO=y   # 33 MB, CPU-only here
# BR2_PACKAGE_REALESRGAN_NCNN_VULKAN_MODEL_ANIME=y   # 9 MB, CPU-only here
# 4. the runtime lib the engine links and the image does not ship
#    board/desktop/post-build.sh, the host-runtime-lib list:
#      for so in libdisplay-info.so.3 ... libgomp.so.1; do
# 5. recompose the defconfigs, run the gate, build, boot, right-click
python3 tools/pengyos-recipe-lint -m pengyos_recipe_lint check buildroot-external/package
scripts/compose-defconfigs.py --check
python3 scripts/pengyos_gate.py run --repo . --mode full
python3 -m pengyos_build build pengyos-desktop --targets ../../targets.toml
python3 -m pengyos_qemu_verify boot ~/build/pengyos/output/pengyos-desktop/images/<img> --uefi
```

Lane 2 is allowed in Server too; Core is Lane 1 only. The recipe is
x86_64-gated because upstream publishes `linux` (x86_64), `macos` and `windows`
builds and nothing else.

## What the recipe installs

| Path | From | Why there |
|---|---|---|
| `/usr/lib/pengyos/upscale/realesrgan-ncnn-vulkan` | the vendor archive | Lane 2: an isolated prefix, not `/usr/bin` |
| `/usr/share/pengyos/upscale/models/*` | the vendor archive | data, selected by the model symbols |
| `/usr/bin/pengyos-upscale` | this package dir | the command with the contract |
| `/usr/share/cosmic/com.system76.CosmicFiles/v1/context_actions` | this package dir | COSMIC Files' system-default right-click actions |
| `/usr/share/applications/io.catbee.pengyos.Upscale.desktop` | this package dir | the "Open with" entry |

**The recipe installs the integration, not just the binary.** That is the
design decision, and it is what keeps the image honest: the menu entries exist
exactly when the engine exists. A `rootfs-overlay` copy of `context_actions`
would ship "Upscale 4×" in every Desktop image, including the ones built with
the package off, pointing at a binary that is not there — the silent gap the
`test_cosmic_app_wiring.py` tests exist to catch.

## Why one recipe and not the engine/model pair

The whistle prototype splits engine and model into two recipes because they
version independently and come from two vendors. Here the vendor ships the
binary and the model graphs in **one release archive**, so one pin covers both
and a model refresh cannot be separated from a binary refresh. Splitting them
would download the same 47 MB archive twice.

## The pins

`realesrgan-ncnn-vulkan.hash` pins the release archive
(`realesrgan-ncnn-vulkan-20220424-ubuntu.zip`, v0.2.5.0 — the newest release
that publishes a Linux build; v0.3.0 publishes Python code and `.pth` weights,
no ncnn binary) and the `LICENSE` from the same tag, which the archive does not
carry.

Provenance check performed on 2026-10-15: the archive's `realesrgan-ncnn-vulkan`
and all eleven model files hash identically to the copies the local
`skills/upscale/` tree has used since 2026-06-19, so the pin is the same bytes
that were measured, not a re-download of something similar.

## Checks to run before this is credible

```sh
utils/check-package buildroot-external/package/realesrgan-ncnn-vulkan/*
python3 tools/pengyos-recipe-lint -m pengyos_recipe_lint check buildroot-external/package
python3 scripts/pengyos_gate.py run --repo . --mode full
scripts/compose-defconfigs.py --check
python3 -m pengyos_build build pengyos-desktop --targets ../../targets.toml
python3 -m pengyos_qemu_verify boot <img> --uefi
# in the running session: right-click an image, watch the notification,
# and check the file lands next to the original
```

## Known costs, stated up front

- **Not reproducible from source.** The binary is the vendor's; `pengyos-repro`
  cannot rebuild it. The SBOM names it as an upstream artifact.
- **`libgomp.so.1` becomes an image dependency** (see README). It is a GCC
  runtime library copied from the build host, which is the same debt COSMIC's
  host-built binaries already carry.
- **The 2022 binary is old** and the vendor has published nothing newer. The
  nightly observer watches the repo; a new release is a new pin, not a
  version-string bump.
- **The big models do not allocate on the NVIDIA devices measured here.** If
  the Desktop ships them, the menu action will fail on those machines with a
  notification, not a result. Ship the fast model unless there is a reason.
