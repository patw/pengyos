# pengyos-upscale — Real-ESRGAN image upscaling for the Desktop (prototype)

Status: **prototype, not shipped.** This directory holds the measured
evaluation of Real-ESRGAN as PengyOS's image upscaler, a working
`pengyos-upscale` command, the Buildroot recipe that installs it *and* its
shell integration, and the numbers behind the recommendation. Nothing here is
enabled in an image.

## What it is

[Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) compiled against
[Tencent/ncnn](https://github.com/Tencent/ncnn) with the **Vulkan** backend:
one portable binary that runs a super-resolution network on any Vulkan device —
Intel/AMD through Mesa, NVIDIA through the NVIDIA edition, and Mesa's `llvmpipe`
as the CPU fallback. No CUDA, no Python, no PyTorch. The Desktop layer already
ships `vulkan-loader` and the Mesa Vulkan drivers, so the GPU path is already
in the image.

## Measured on this box (behoserver, 2× RTX 5060 Ti, 32 CPUs)

Device list as the engine enumerates it: `0` and `1` are the two NVIDIA cards,
`2` is `llvmpipe` (CPU). Full data: [`results/measured.json`](results/measured.json),
produced by [`results/measure.py`](results/measure.py).

| Model | Device | Input | Wall | Peak RSS | Result |
|---|---|---|---|---|---|
| `realesr-animevideov3` | auto (GPU) | 512×512 → 2× | 1.6 s | 287 MB | ok |
| `realesr-animevideov3` | auto (GPU) | 512×512 → 4× | 1.9 s | 308 MB | ok |
| `realesr-animevideov3` | auto (GPU) | 1920×1080 → 2× | 4.1 s | 342 MB | ok |
| `realesr-animevideov3` | auto (GPU) | 1920×1080 → 4× | 7.2 s | 529 MB | ok |
| `realesr-animevideov3` | auto (GPU) | 3840×2160 → 4× | 24.5 s | 1259 MB | ok |
| `realesr-animevideov3` | llvmpipe | 512×512 → 4× | 2.7 s | 314 MB | ok |
| `realesrgan-x4plus-anime` | auto (GPU) | 512×512 → 4× | 2.5 s | 473 MB | **`vkAllocateMemory failed -2`, SIGSEGV** |
| `realesrgan-x4plus` | auto (GPU) | 512×512 → 4× | 2.7 s | 1063 MB | **`vkAllocateMemory failed -2`, SIGSEGV** |
| `realesrgan-x4plus` | llvmpipe | 512×512 → 4× | 47.6 s | 2240 MB | ok |
| `realesrgan-x4plus` | llvmpipe | 1920×1080 → 4× | 359 s | 2480 MB | ok |

Three things the numbers say:

1. **The fast model is the only practical default.** 1080p → 4K in 7 seconds on
   the GPU is a right-click action. The "best quality" photo model does not
   allocate on these GPUs at all, and on the CPU it takes six minutes for the
   same image. That matches the operator's earlier verdict on the 2026-06
   ESRGAN experiment ("too slow for practical use") — the speed problem was the
   *model*, not the engine.
2. **The big models fail on the NVIDIA devices here, not on the CPU.**
   `vkAllocateMemory failed -2` (`VK_ERROR_OUT_OF_DEVICE_MEMORY`) on devices 0
   and 1, success on `llvmpipe`. The 1.2 MB model allocates fine; the 9 MB and
   33 MB models do not. Unverified as to which side is at fault (the 2022
   prebuilt binary, the ncnn allocator's `DEVICE_LOCAL` request, or the NVIDIA
   driver's device-local limit) — the honest statement is the measurement.
3. **The engine's failure modes are not exit codes.** A decode failure exits
   **0** and writes nothing; an allocation failure dies with **SIGSEGV**. A
   right-click action shows no output, so without a wrapper a failed upscale is
   indistinguishable from a successful one.

## Where it integrates

The Desktop is COSMIC, and COSMIC Files has had **user-defined right-click
actions** since epoch 1.0.10 (`src/context_action.rs`: `name`, `confirm`,
`selection`, `steps`). The pinned Desktop set (epoch 1.9, commit
`d1f0af78`) has the file, so **no patch is needed**. The actions are read from
cosmic-config's `context_actions` key, and libcosmic resolves a key as
*user file first, then the system default* at
`$XDG_DATA_DIRS/cosmic/<APP_ID>/v1/<key>` — which is the same mechanism the
image already uses for the compositor's xkb config and the wallpaper.

| Surface | File | Notes |
|---|---|---|
| **Right-click → "Upscale 2×" / "Upscale 4×"** | `/usr/share/cosmic/com.system76.CosmicFiles/v1/context_actions` | System-wide, no per-user file, overridable by a user's own `context_actions`. `selection: Files` = shown when the selection has no folder |
| **"Open with" → Image Upscaler** | `/usr/share/applications/io.catbee.pengyos.Upscale.desktop` | `MimeType=image/*` + `NoDisplay=true`: appears for images, not in the app grid |
| **Terminal / scripts / Pengy** | `/usr/bin/pengyos-upscale` | The contract in [`spec.md`](spec.md) |
| `mimeapps.list` | **not touched** | The default app for an image is the viewer. An upscaler is an action on a file, not a viewer |

Two rough edges of the mechanism, both handled:

- Context actions are **not MIME-filtered** — `selection: Files` shows the entry
  on a `.txt` too. The guard is the command: exit 2 with a notification naming
  the formats it accepts.
- `steps` run with **no shell** (`shlex` + `Command`, `spawn_detached`):
  absolute paths only, no `~`, no globs, no pipes, and **no output window**.
  Hence the absolute path in the action and the `notify-send` report from the
  wrapper.

## Lane, and why not Lane 1

**Lane 2** (SPEC §2.5): the vendor's own prebuilt binary, pinned by sha256,
installed under an isolated prefix (`/usr/lib/pengyos/upscale`), with the model
data in `/usr/share/pengyos/upscale/models`. BSD-3-Clause on the code and the
models, which is in the permissive family the license audit accepts.

Lane 1 (build it ourselves) is not available today, measured against the pinned
Buildroot 2026.08 tree:

- **no `ncnn` recipe at all** in `package/`;
- **no target `glslang`** — ncnn compiles its Vulkan shaders with
  `glslangValidator` at build time, and Buildroot ships only `python-glslang`,
  which is a **host** package;
- `spirv-tools`, `spirv-headers`, `vulkan-headers`, `vulkan-loader` and Mesa are
  present, so the missing pieces are ncnn itself and a target glslang.

So a Lane 1 path means authoring two recipes (glslang + ncnn) before the
upscaler recipe. That is a real option, not a quick one; the divergence entry
records the debt in the meantime.

## The one dependency the image does not have

`ldd` on the vendor binary:

```
libc.so.6  libgcc_s.so.1  libgomp.so.1  libm.so.6  libmvec.so.1  libvulkan.so.1
```

The Desktop target ships every one of those **except `libgomp.so.1`** (checked
in a built tree: `output/pengyos-desktop/target/usr/lib` has `libgcc_s`,
`libmvec`, `libstdc++`, `libvulkan*`, no `libgomp`; the toolchain output has no
libgomp at all). The engine does not start without it.

The fix is the pattern the Desktop post-build already uses for six other
runtime libs the host-built binaries need — copy the host's `libgomp.so.1` into
the image:

```sh
for so in libdisplay-info.so.3 ... libgomp.so.1; do
```

The alternative is a Lane 1 ncnn build with OpenMP off, which is the same
two-recipe cost as above.

## Landing plan

Nothing in this directory is installed. To enable it (see
[`recipe/README.md`](recipe/README.md) for the exact commands):

1. Move `recipe/realesrgan-ncnn-vulkan/` into `buildroot-external/package/`,
   `source` its `Config.in` from `buildroot-external/Config.in`.
2. Add to the **desktop** layer (`configs/layers/desktop.config`):
   `BR2_PACKAGE_REALESRGAN_NCNN_VULKAN=y` (+ the model symbols you want).
3. Add `libgomp.so.1` to the host-runtime-lib list in
   `board/desktop/post-build.sh`.
4. `scripts/compose-defconfigs.py`, run the gate, build, boot under QEMU, and
   right-click an image.
5. Record the divergence in `divergence.toml` (proposal in
   `recipe/divergence-proposal.toml`) and re-run the license audit.

## What is decided here, and what is the operator's call

Decided by measurement: the default model, the accepted input formats, the
output naming, the exit codes, the libgomp gap, the Lane choice.

The operator's call, per Principle 8/9: whether the Desktop ships an upscaler
at all, which models the image carries (the photo model is 33 MB of weights for
a path that takes six minutes on a CPU), and the menu labels.

## Files

```
pengyos-upscale                    the command (POSIX sh)
spec.md                            the contract
recipe/realesrgan-ncnn-vulkan/     the Buildroot recipe
  ├── Config.in  ├── .mk  ├── .hash
  ├── pengyos-upscale            → /usr/bin/pengyos-upscale
  ├── context_actions            → /usr/share/cosmic/com.system76.CosmicFiles/v1/
  └── io.catbee.pengyos.Upscale.desktop → /usr/share/applications/
recipe/README.md                   how to land it, with the measurements behind it
recipe/divergence-proposal.toml    the ledger entry to paste when it is enabled
results/measure.py                 the measurement harness
results/measured.json              the numbers above
tests/test_upscale.py              the contract + the wiring + the behaviour
```

## Running it now (before any image ships it)

```sh
export PENGYOS_UPSCALE_ENGINE=~/Personal/skills/upscale/realesrgan-ncnn-vulkan
export PENGYOS_UPSCALE_MODELS=~/Personal/skills/upscale/models
tools/pengyos-upscale/pengyos-upscale -s 2 photo.jpg
python3 -m pytest -q tools/pengyos-upscale     # behaviour layer runs too
```
