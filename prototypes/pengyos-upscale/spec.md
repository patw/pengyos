# pengyos-upscale — contract for neural image upscaling

## Summary

Give a PengyOS Desktop user an image upscaler they can reach from the file
manager — right-click, pick a scale, get a file next to the original — with one
command behind it that has a stable contract, so the menu entry, a terminal and
any script all call the same thing and see the same exit codes.

## Purpose

The Desktop decodes images (COSMIC Viewer) and it can draw them bigger
(Interpolation::Bicubic in the viewer, lanczos in any converter). What it has
no way to do is *reconstruct* detail: a 480 px photo upscaled 4× by
interpolation is a soft 1920 px photo. Real-ESRGAN running on ncnn's Vulkan
backend does that on the GPUs this image already drives, with no CUDA, no
Python and no PyTorch.

The bar for the OS is the same bar every component is held to: runs on the
hardware the image supports, no network, small enough to ship, permissively
licensed, pinned by hash, predictable enough that a caller can rely on its exit
codes, and *visible when it fails* — because a right-click action has no window
to show an error in.

## Deliverable

`pengyos-upscale`: a POSIX shell command that upscales images with the pinned
Real-ESRGAN ncnn-vulkan binary, plus the shell integration that makes it
reachable from COSMIC Files.

## Interface

```
pengyos-upscale [-s 2|3|4] [-n MODEL] [-f auto|png|jpg|webp] [-o OUT]
                [-g GPU] [-t TILE] [-x] [-N] [-q] FILE [FILE...]
```

| Input | Behaviour |
|---|---|
| `FILE...` | one or more images; `jpg`, `jpeg`, `png`, `webp`, `ppm`, `pgm` (measured: the engine decodes exactly these) |
| `file://…` | accepted and stripped: a `.desktop` launcher passes `%U` as a URI |
| `-s N` | 2, 3 or 4. Default 4 |
| `-n MODEL` | `realesr-animevideov3` (default), `realesrgan-x4plus-anime`, `realesrgan-x4plus` |
| `-f FMT` | `auto` (default: keep the input's format; `ppm`/`pgm` have no matching encoder so they become `png`), `png`, `jpg`, `webp` |
| `-o OUT` | one input → an output file; several inputs → an output directory (created if missing) |
| `-g N` | Vulkan device index; default is the engine's own pick |
| `-t N` | tile size, pixels; 0 = auto |
| `-x` | TTA mode: slower, marginally better |
| `-N` | no desktop notification |
| `-q` | no per-file stdout |

Default output is `<input dir>/<name>_x<scale>.<fmt>`, written next to the
input. A context action has no dialog to ask where the result goes, so the
answer is decided here and is the same every time. Two inputs that would land
on the same name (`a.jpg` + `a.jpeg` with `-f jpg`) are numbered `_2`, `_3`.

| Exit | Meaning |
|---|---|
| 0 | every input produced an output file |
| 2 | an input is missing, unreadable, or not a format the engine decodes |
| 3 | engine or model files missing |
| 4 | the engine ran and produced nothing |
| 5 | usage error |

Paths are overridable by environment (`PENGYOS_UPSCALE_ENGINE`,
`PENGYOS_UPSCALE_MODELS`, `PENGYOS_UPSCALE_MODEL`, `PENGYOS_UPSCALE_SCALE`,
`PENGYOS_UPSCALE_FORMAT`, `PENGYOS_UPSCALE_GPU`, `PENGYOS_UPSCALE_TILE`,
`PENGYOS_UPSCALE_NOTIFY`) so the prototype runs before the image installs the
engine at `/usr/lib/pengyos/upscale/` and the models at
`/usr/share/pengyos/upscale/models/`.

## Behaviour contract

- **A failure is never silent.** The vendor binary reports a decode failure
  with **exit status 0 and no output file**, and reports a Vulkan allocation
  failure by dying with **SIGSEGV (139)** — both measured. The wrapper treats
  "no output file" as the only evidence that matters, prints the cause, and
  raises a `notify-send` notification. This is the reason the wrapper exists.
- **The output is a new file.** Nothing is overwritten, nothing is moved, no
  in-place edit. A user can always see what happened.
- **Offline.** The engine loads a model from disk and writes a file. No
  network, no telemetry, no model download at run time.
- **Deterministic.** Same input, same model, same scale, same flags → same
  result. Tile size changes the result slightly (the engine crops into tiles
  and stitches), which is why `-t` is exposed rather than hidden.
- **The default model is the one that runs on the GPU.** Measured on this box:
  `realesr-animevideov3` runs on the discrete GPU in 4.1 s for 1080p→4K;
  `realesrgan-x4plus` and `realesrgan-x4plus-anime` fail to allocate on the
  same devices (`vkAllocateMemory failed -2`) and only run on llvmpipe, at
  47 s for 512²→2048² and 359 s for 1080p→4K. Shipping the "best quality"
  model as the default would ship a feature that appears to hang.

## Shell integration

Three surfaces, all installed by the recipe, so enabling the package is the
whole feature and disabling it removes the whole feature:

| Surface | File | Mechanism |
|---|---|---|
| Right-click in COSMIC Files | `/usr/share/cosmic/com.system76.CosmicFiles/v1/context_actions` | cosmic-files user-defined context actions (`src/context_action.rs`), read as cosmic-config's system default |
| "Open with" in COSMIC Files | `/usr/share/applications/io.catbee.pengyos.Upscale.desktop` | `MimeType=` + `NoDisplay=true`: listed for images, absent from the app grid |
| Terminal and scripts | `/usr/bin/pengyos-upscale` | the command itself |

`mimeapps.list` is deliberately **not** touched: the default application for an
image is the viewer, and an upscaler is not a viewer.

## Acceptance criteria

1. `pengyos-upscale photo.jpg` writes `photo_x4.jpg` next to the input and
   exits 0. (Tested.)
2. A non-image, a missing engine and a missing model are exits 2, 3 and 3.
   (Tested.)
3. An engine that produces no file is exit 4, leaves no truncated output, and
   says why. (Tested; skips on a box where the allocation succeeds.)
4. Names with spaces, a batch, `-o dir`, `-o file` and a `file://` URI all
   work. (Tested.)
5. The right-click entries appear in COSMIC Files on a Desktop image with the
   package enabled, and are absent when it is disabled. (Needs a build.)
6. The recipe passes `pengyos-recipe-lint` and the repo gate. (Passes.)
7. The image gains no new runtime dependency it does not already ship, except
   `libgomp.so.1`, which post-build copies the way it already copies six other
   host runtime libs. (Measured with `ldd`.)
