# Recipe skeleton — how this lands in `buildroot-external/package/`

These are **not installed**. They live here while the component is a prototype.
To enable it, move the two directories into
`buildroot-external/package/`, `source` their `Config.in` files from
`buildroot-external/Config.in`, add them to `external.mk`, and set
`BR2_PACKAGE_NEEDLE_ENGINE=y` + `BR2_PACKAGE_WHISTLE_MODEL=y` in the
**desktop** layer (`configs/layers/desktop.config`) — Lane 2 is allowed in
Server too; Core is Lane 1 only.

## The pair

| Recipe | Lane | Installs | Pinned by |
|---|---|---|---|
| `needle-engine/` | **2 — upstream artifact** | `/usr/lib/pengyos/stt/needle` | sha256 in `.hash` |
| `whistle-model/` | pinned data | `/usr/share/pengyos/stt/whistle.cact` | sha256 in `.hash` |

Both Apache-2.0. Both download over `https` from the vendor's published release,
which is what `pengyos-recipe-lint`'s `no_unverified_downloads` rule wants.

## Why two recipes and not one

The engine is a binary with no source; the model is data. They version
independently (engine 3.3.1, model 2.0.0), they have different sizes (1.5 MB vs
16.9 MB), and a model refresh must not look like a code change in the SBOM.
Keeping them apart also lets an image ship the engine with a keyword-tuned model
built later without touching the engine pin.

## The arch guard

Upstream publishes prebuilt engines for `linux-x86_64`, `linux-arm64`,
`linux-armv7`, `linux-riscv64` and `linux-mipsel`. `BR2_PACKAGE_NEEDLE_ENGINE_ARCH_OK`
defaults to y for exactly those, and the install command fails the build on
anything else. A recipe that silently installed nothing on, say, powerpc is the
failure mode this guards.

## Filling in the remaining pins

`needle-engine.hash` carries the x86_64 digests measured on 2026-10-15. Before
enabling another architecture, add that arch's digests from the same release:

```sh
for a in arm64 armv7 riscv64 mipsel; do
  curl -sL -o /data/tmp/n-$a "https://huggingface.co/Cactus-Compute/needle3/resolve/main/linux-$a/needle"
  sha256sum /data/tmp/n-$a
done
```

## Checks to run before this is credible

```sh
# 1. linter clean
utils/check-package buildroot-external/package/needle-engine/* buildroot-external/package/whistle-model/*
# 2. the symbol resolves against the pinned Buildroot tree
python3 tools/pengyos-recipe-lint -m pytest -q      # the linter's own suite
python3 scripts/pengyos_gate.py run --repo . --mode full
# 3. the defconfigs recompose after editing the layer
scripts/compose-defconfigs.py --check
# 4. build it and boot it — the unit tests are not enough
python3 -m pengyos_build build pengyos-desktop --targets ../../targets.toml
python3 -m pengyos_qemu_verify boot ~/build/pengyos/output/pengyos-desktop/images/<img> --uefi
# 5. record the divergence (see divergence-proposal.toml) and re-run the license audit
```

## What the image needs that is already there

The Desktop layer already ships PipeWire, WirePlumber, ALSA UCM profiles,
`pw-record`/`pw-play`/`pw-cat`, and the kernel fragment builds `snd-usb-audio`
and the Intel SOF stack. Speech input needs no new audio plumbing — only the
engine, the model, and a keyword file.
