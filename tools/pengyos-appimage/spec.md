# PengyOS appimage — inspect, integrate and check AppImages

## Summary
Make AppImages usable on PengyOS without running or unpacking them: say what one
is, put it in the app launcher where it sits, and name the libraries it needs
that the system lacks.

## Purpose
SPEC §2.5, Lane 3: an AppImage already runs in place (static type-2 runtime,
FUSE). What is missing is integration and a diagnosis when one will not start
(a Qt platform-plugin dialog instead of "missing libxcb-cursor.so.0").

## Deliverable
A Python CLI `pengyos-appimage` with `inspect|integrate|check`, standard library
only, that starts no process and writes nothing but the launcher entry and icon.

## Interface
- `pengyos-appimage inspect APP.AppImage [--json]`
- `pengyos-appimage integrate APP.AppImage [--remove] [--dest DIR] [--json]`
- `pengyos-appimage check APP.AppImage [--libdirs DIR...] [--json]` (exit 4 if
  anything is missing)

## Verification (tests are first-class)
- The runtime parser finds the payload offset of a real type2-runtime fixture,
  and (opt-in) agrees with each real AppImage's own `--appimage-offset`.
- The squashfs reader returns byte-identical files, symlinks and directories
  for `mksquashfs` fixtures in every supported compressor and layout, and
  (opt-in) for real AppImages against `unsquashfs`.
- The ELF parser reads `DT_NEEDED` from PIE, non-PIE and shared objects, and
  (opt-in) agrees with `objdump -p`.
- `integrate` writes an entry that passes `desktop-file-validate`, whose `Exec`
  runs the AppImage by absolute path, and `--remove` deletes only its own files.
- `check` reports a library shipped nowhere, resolves a bundled one behind a
  symlink, and reports a `dlopen` dependency from its table.
- No command starts a process: with every process-starting call patched to
  raise, all three succeed against a fixture whose `AppRun` is a poison hook.

**GitHub-ready contract** (asserted by `tests/test_github_ready.py`):

* **README.md** is authored (install instructions, usage of all three
  subcommands with their `--remove`/`--libdirs`/`--json` flags, the "never runs
  or unpacks" invariant, and the MIT license line with the
  required copyright).
* **LICENSE** is MIT with "Copyright (c) 2026 Pat Wendorf" and its core
  clauses intact; `pyproject.toml` declares the same license.
* **CHANGELOG.md** is Keep a Changelog / Semantic Versioning formatted, and
  its topmost *dated* `## [version] - date` entry equals the package
  `__version__` (a release without a changelog entry fails the suite). The
  separator may be a hyphen or an em dash.
* **`.gitignore`** covers Python build/test noise and virtual environments
  and real AppImages (`*.AppImage`), and none of those
  artifacts are tracked by git.
* **Docs in sync** — every `*.py` in the package and every `test_*.py` in
  `tests/` is documented in the README's project-layout tree.
* **Full green test suite** — enforced by the outer `pytest -q` gate itself;
  an **opt-in** integration test behind `PENGYOS_IT=1` additionally re-runs
  the suite in a subprocess (with `PENGYOS_IT` removed from the child
  environment so it cannot recurse) and asserts exit `0`.

## Acceptance criteria
- It never runs or unpacks an AppImage, and starts no process.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building, updating or sandboxing AppImages.
- Symbol-version checks (`check` compares sonames only).
- lzo and lz4 payloads.

## Constraints
- Runs as a non-root user with no network at test time, on committed fixtures
  that are real tool output. Real AppImages are opt-in via `PENGYOS_IT=1` and
  `PENGYOS_APPIMAGE_REAL`.
