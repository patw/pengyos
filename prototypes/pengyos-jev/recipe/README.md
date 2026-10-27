# Recipe skeleton — how this lands in `buildroot-external/package/`

These are **not installed**. They live here while the component is a prototype.
To enable: move both directories into `buildroot-external/package/`, `source`
their `Config.in` files from `buildroot-external/Config.in`, add them to
`external.mk`, and set `BR2_PACKAGE_LLAMA_CPP_VK=y` + `BR2_PACKAGE_LAYA_MODEL=y`
in the **desktop** layer (`configs/layers/desktop.config`). Core is Lane 1 only;
the engine is Lane 1 (source), the weights are pinned data.

## The pair

| Recipe | Lane | Installs | Pinned by |
|---|---|---|---|
| `llama-cpp-vk/` | **1 — source** | `/usr/bin/llama-server` (static) | release tag `b11318` |
| `laya-model/` | pinned data | `/usr/share/pengyos/jev/Laya-Q8_0.gguf` | sha256 in `.hash` |

Apache-2.0 weights, MIT engine. Split for the same reason as the Whistle pair:
the engine and the weights version independently, and a model refresh must not
look like a code change in the SBOM.

## The two Buildroot gaps (measured 2026-10-15)

1. **No `host-shaderc`.** `ggml/src/ggml-vulkan/CMakeLists.txt` does
   `find_package(Vulkan COMPONENTS glslc REQUIRED)`; Buildroot 2026.08 has
   `spirv-headers` and `spirv-tools` but **no shaderc/glslc recipe at all**, and
   the stock `llama-cpp` recipe's Vulkan option depends only on
   `vulkan-loader`. Enabling `BR2_PACKAGE_LLAMA_CPP_VULKAN=y` in 2026.08
   therefore fails at configure. We worked around it on the host by extracting
   the Ubuntu `glslc` + `libshaderc1` + `spirv-headers` debs into a user prefix
   (`dpkg -x`, no root) and pointing CMake at it; the distro fix is a
   `host-glslc` recipe (shaderc source, cmake, links glslang/spirv-tools, all
   present in 2026.08).
2. **The `llama-cpp` pin is too old for the decision API.** 2026.08 ships
   `b8117` (2026-02-20). `/v1/systemone` landed 2026-10-02 (`a4cb4c61f`,
   first tag `b11318`). A decision service needs the newer pin — hence a
   separate `llama-cpp-vk` recipe rather than flipping the stock symbol.

Also measured: PengyOS has **no `libgomp.so.1`** in the rootfs, so a build with
OpenMP on produces a binary that will not start. `-DGGML_OPENMP=OFF` is
required (the cost on a 4-core N100 was not measured; the CPU path there used
4 threads and hit 405 ms/decision).

## Checks to run before this is credible

```sh
# 1. linter clean
utils/check-package buildroot-external/package/llama-cpp-vk/* buildroot-external/package/laya-model/*
# 2. symbols resolve against the pinned tree + recipe lint
python3 tools/pengyos-recipe-lint -m pytest -q
python3 scripts/pengyos_gate.py run --repo . --mode full
# 3. defconfigs recompose after editing the layer
scripts/compose-defconfigs.py --check
# 4. build it and boot it — the unit tests are not enough
python3 -m pengyos_build build pengyos-desktop --targets ../../targets.toml
python3 -m pengyos_qemu_verify boot ~/build/pengyos/output/pengyos-desktop/images/<img> --uefi
# 5. license audit (Laya Apache-2.0 passes; d1's LFM Open License needs an
#    exception entry; OpenJev's CC-BY-NC-4.0 must stay out)
```

## What the image already has

`vulkan-loader`, Mesa ANV (Intel) / RADV (AMD) / llvmpipe (CPU fallback) in the
Desktop layer; NVIDIA's Vulkan ICD in the NVIDIA editions. COSMIC already
requires Vulkan, so the decision service adds **no new graphics plumbing** —
only the engine, the weights, a user, and a unit.
