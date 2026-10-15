# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added — prototype: Real-ESRGAN as the Desktop image upscaler
- **Measured evaluation** (`results/measure.py` → `results/measured.json`) on a
  box with two RTX 5060 Ti and Mesa llvmpipe: the fast model
  `realesr-animevideov3` upscales 1920×1080 → 3840×2160 in **4.1 s at 2×** and
  **7.2 s at 4×** on the GPU (342–529 MB peak RSS); `realesrgan-x4plus` and
  `realesrgan-x4plus-anime` **fail to allocate on the NVIDIA devices**
  (`vkAllocateMemory failed -2`, SIGSEGV) and run only on llvmpipe, at 47.6 s
  for 512²→2048² and 359 s for 1080p→4K.
- **`pengyos-upscale`** — POSIX shell command, images in, files out, next to
  the input. Exit codes 0/2/3/4/5; `file://` URIs accepted; batch, `-o file`,
  `-o dir`, names with spaces, output-collision numbering; `notify-send`
  report, because a context action shows no output.
- **The engine's failure modes are not exit codes** (measured): a decode
  failure exits **0** and writes nothing, an allocation failure dies with
  **SIGSEGV (139)**. The wrapper treats "no output file" as the evidence, which
  is the whole reason it exists.
- **A Buildroot recipe** (`recipe/realesrgan-ncnn-vulkan`): Lane 2 prebuilt
  binary pinned by sha256 under `/usr/lib/pengyos/upscale`, model graphs pinned
  under `/usr/share/pengyos/upscale/models`, BSD-3-Clause, x86_64 guard, model
  sub-symbols so the 33 MB photo model is a choice and not a default. Passes
  `pengyos-recipe-lint`.
- **Shell integration installed by the recipe**: COSMIC Files right-click
  actions at `/usr/share/cosmic/com.system76.CosmicFiles/v1/context_actions`
  (the mechanism epoch 1.0.10 added; the pinned epoch 1.9 commit has
  `src/context_action.rs`, so no patch), an `Open with` entry
  (`MimeType=image/*`, `NoDisplay=true`), and the command. `mimeapps.list` is
  deliberately untouched.
- **Two findings that decide the landing**: the engine links `libgomp.so.1`,
  which the Desktop target does not ship (the toolchain output has none), so
  post-build must copy it the way it already copies six other host runtime
  libs; and Buildroot 2026.08 has **no ncnn recipe and no target glslang**, so
  Lane 1 is a two-recipe project, not a recipe.
- **Tests** (`tests/test_upscale.py`): recipe honesty, the wiring agreement
  between the recipe's install paths and the integration files, the usage
  contract without an engine, and — when an engine is provided — a real
  upscale, a silent engine failure turned into exit 4, spaces, collisions,
  batches and URIs.

### Notes
- Nothing here is enabled in an image. The recipe stays in `recipe/` until the
  operator decides the Desktop ships an upscaler, which models it carries, and
  what the menu says (spec Principle 8/9).
- The COSMIC Files context-action mechanism was confirmed against the pinned
  commit `d1f0af78` (`src/context_action.rs`, `Config::context_actions`) and
  the system-default resolution order against the pinned libcosmic
  `87ab8179` (`Config::get` → `get_local` then `get_system_default`,
  `xdg::BaseDirectories::with_prefix("cosmic")`).
