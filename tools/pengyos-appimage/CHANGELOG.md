# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-10-25

Rebuilt as a small helper for AppImages that run in place: `inspect`,
`integrate`, `check`. It starts no process and unpacks nothing.

### Removed

- `extract` and `run`. An AppImage already runs in place on PengyOS; unpacking
  each app to a prefix duplicated it with no update path, needed `unsquashfs`
  (not in the image), and `run` skipped `AppRun`, which linuxdeploy-style
  AppImages need to set up their library and plugin paths. Its sandbox gave the
  app an empty home, so it could neither open nor save files.
- The bsdtar/unsquashfs/7z extraction layer (bsdtar cannot read squashfs; 7z
  takes no offset) and the MIME-package XML `integrate` wrote (an app must not
  declare MIME types; `MimeType=` in its entry is what associates files).

### Added

- `check`: reads `DT_NEEDED` from every ELF object in the payload and names the
  libraries neither the AppImage nor the host provides, which objects need
  them, and a hint. A small table adds `dlopen()` dependencies (Qt 6's xcb
  plugin → `libxcb-cursor.so.0`). Exit status 4 when anything is missing.
- `squashfs.py`: a read-only squashfs 4.0 reader in pure Python (gzip, xz,
  lzma, zstd via `compression.zstd`), bounded against hostile images. It
  matches `unsquashfs` byte for byte on real AppImages.
- `elf.py`: `DT_NEEDED`/`DT_SONAME` from headers, mapping `DT_STRTAB` through
  the `PT_LOAD` segments (pengyos-debx treats it as a file offset, which reads
  garbage in a non-PIE executable).
- `integrate --remove`, matched on an `X-PengyOS-AppImage` marker so it works
  after the AppImage is deleted.

### Changed

- `integrate` takes the AppImage itself, not an extracted directory, and points
  every `Exec` (and `TryExec`) at its absolute path; before, it copied the
  payload's `Exec=app %F`, which named a program that is not on `PATH`. The
  entry is `appimage-<id>.desktop` and the icon goes to a size hicolor declares.
- `inspect` reads the payload in place and also reports its compression.

### Fixed

- Every real AppImage failed with "suspicious trailer size 0". The parser
  modelled a format that does not exist: an ELF *trailer* at the end of the
  file carrying `X-AppImage-*` keys. A type-2 AppImage is the runtime ELF at
  offset 0 (`AI\x02` in `e_ident`) followed by the squashfs; the tests passed
  only because the fixture was built to the same invented layout. `trailer.py`
  is replaced by `runtime.py`, which finds the payload as the runtime does
  (`appimage_get_elf_size`), checks the `hsqs` magic there, and reads
  `.upd_info` and `.sha256_sig`.
- Fixtures are real tool output: the sample AppImage is the upstream
  type2-runtime with a `mksquashfs` payload of `gcc`-built ELF objects, and the
  squashfs fixtures cover every compressor and layout the reader handles.

## [0.1.0] — 2026-02-11

### Added

- Scaffold the repository: src-layout Python package `pengyos_appimage`,
  `pyproject.toml` (stdlib-only, `pengyos-appimage` console entry point),
  README, LICENSE (MIT), CHANGELOG, and `.gitignore`.
- `pengyos-appimage` CLI with `inspect` / `extract` / `integrate` / `run`
  subcommands wired through an explicit dispatch table. Handlers are honest
  placeholders that raise `NotYetImplementedError` until implemented.
- Smoke + unit test suite (`pytest -q` green).

## [Unreleased]

### Added

- `inspect` subcommand: read the AppImage type and extract the `.desktop` + icon
  **without executing** the payload. Implemented as stdlib-only modules:
  - `trailer.py` — parse the ELF trailer metadata (`Format`/`Type`/`Arch`/
    `X-AppImage-Path`/`X-AppImage-Payload-Offset`) via `struct`; never runs the
    AppImage.
  - `desktop.py` — parse freedesktop `.desktop` files (sections, keys,
    localised `Name[lang]=` values).
  - `squashfs.py` — extract the embedded squashfs payload with a trusted tool
    (`bsdtar`/`unsquashfs`/`7z`) behind a mockable `SquashfsTool.run` seam.
  - `inspect.py` — orchestrate the above into an `InspectResult`; the CLI
    renders a human report or `--json`.
- `pengyos-appimage extract` subcommand: extract the AppImage payload into a
  prefix and validate its structure (present ``AppRun`` entry point,
  ``.desktop`` file, declared ``X-AppImage-Root-Directory``). Implemented as a
  stdlib-only `extract.py`; the embedded hooks are never executed. Structural
  problems are reported as warnings; an empty or unreadable extraction is a
  fatal error. The CLI prints a human report and exits non-zero on error.
- `pengyos-appimage integrate` subcommand: generate the freedesktop
  integration entries for an *extracted* app prefix — a validated `.desktop`
  file (in `<dest>/applications/`), the app icon (in
  `<dest>/icons/hicolor/<size>x<size>/apps/`), and a MIME declaration (in
  `<dest>/mime/packages/` when the `Exec` line accepts URIs). Implemented as a
  stdlib-only `integrate.py`. The embedded hooks are never executed:
  integration only reads the already-extracted files and copies them; there is
  no code path that runs `AppRun`. Structural problems (e.g. a missing icon)
  are reported as warnings; a missing `.desktop` file or a missing required key
  is a fatal error. The CLI prints a human report and exits non-zero on error.
- `desktop.py` gained `render_entry()`, which serialises a single
  `[Desktop Entry]` block back to text (key order and localised `Name[lang]=`
  variants preserved), used by `integrate` to emit a well-formed `.desktop`.
- Committed sample fixture (`tests/fixtures/sample-appimage`, built by
  `build_sample_appimage.py`) with a poison `AppRun` hook; unit tests prove the
  hook is never executed, plus opt-in `PENGYOS_IT=1` real-`unsquashfs`
  integration tests.
- Tests: `test_trailer.py`, `test_desktop.py`, `test_squashfs.py`,
  `test_inspect.py`, `test_extract.py`, `test_integrate.py`, `test_run.py`.
  `pytest -q` = 113 passed, 6 skipped (integration); `PENGYOS_IT=1` = 119,
  exercising real `bwrap`/`unsquashfs`/`desktop-file-validate`.
- `tests/test_github_ready.py` — the spec's *GitHub-ready contract*: asserts
  the authored README (install, usage, the security invariant, license line),
  the MIT LICENSE (with the required copyright and core clauses), the
  `pyproject.toml` license, a Keep a Changelog / SemVer CHANGELOG whose
  topmost dated entry equals the package version, a `.gitignore` that keeps
  build/test noise and the `_appimage_extracted` runtime prefix out of the
  tree (and that such artifacts are not committed), and that every package
  module and test file is documented in the README's project-layout tree
  (docs stay in sync). An opt-in `PENGYOS_IT=1` gate additionally re-runs the
  whole suite in a subprocess and asserts exit 0.
- `pengyos-appimage run` subcommand: launch the extracted app under a
  bubblewrap sandbox **without ever running the embedded `AppRun` hook**.
  Implemented as a stdlib-only `run.py`:
  - `resolve_binary()` finds the *real* binary named by the `.desktop` `Exec`
    line (absolute Exec path looked up under the payload root; a bare name
    searched under `usr/bin`, `bin`, …). When there is no `Exec` line or the
    binary can't be resolved, `run` refuses (`RunError`) rather than falling
    back to `AppRun`.
  - `build_sandbox_argv()` is a pure, documented bubblewrap golden
    (`--ro-bind / / --bind <app_dir> <app_dir> --proc /proc --dev /dev
    --dir /home [--ro-bind /etc/resolv.conf] -- <binary>`).
  - `BwrapTool.run` is the single subprocess seam (mocked in tests);
    `--sandbox`/`--no-sandbox` (mutually exclusive; sandbox is the default) and
    `--json` are wired into the CLI.
  - The poison invariant is proven structurally (the launched argv names the
    real binary, never `AppRun`) and, under `PENGYOS_IT=1`, with real `bwrap`.
