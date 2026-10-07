# PengyOS appimage — integrate & sandbox AppImages

## Summary
Extract an AppImage, generate desktop/mime integration, and run it under bubblewrap.

## Purpose
SPEC §2.5 Lane 3: AppImage as a first-class portable app lane.

## Deliverable
A Python CLI `pengyos-appimage` with `inspect|extract|integrate|run` that never trusts the AppImage's embedded hooks.

## Interface
- pengyos-appimage inspect app.AppImage | extract app.AppImage --to DIR | integrate DIR | run DIR [--sandbox]

## Verification (tests are first-class)
- inspect parses a fixture AppImage header/desktop.
- integrate writes a valid .desktop that `desktop-file-validate`-style checks pass.
- run builds the expected bwrap argv (golden).
- no execution of embedded hooks (asserted by using a fixture with a poison hook that would create a file).

**GitHub-ready contract** (asserted by `tests/test_github_ready.py`):

* **README.md** is authored (install instructions, usage of all four
  subcommands with their `--to`/`--sandbox` flags, the "never executes an
  AppImage's embedded hooks" invariant, and the MIT license line with the
  required copyright).
* **LICENSE** is MIT with "Copyright (c) 2026 Pat Wendorf" and its core
  clauses intact; `pyproject.toml` declares the same license.
* **CHANGELOG.md** is Keep a Changelog / Semantic Versioning formatted, and
  its topmost *dated* `## [version] - date` entry equals the package
  `__version__` (a release without a changelog entry fails the suite). The
  separator may be a hyphen or an em dash.
* **`.gitignore`** covers Python build/test noise and virtual environments
  and the tool's runtime prefix (`_appimage_extracted/`), and none of those
  artifacts are tracked by git.
* **Docs in sync** — every `*.py` in the package and every `test_*.py` in
  `tests/` is documented in the README's project-layout tree.
* **Full green test suite** — enforced by the outer `pytest -q` gate itself;
  an **opt-in** integration test behind `PENGYOS_IT=1` additionally re-runs
  the suite in a subprocess (with `PENGYOS_IT` removed from the child
  environment so it cannot recurse) and asserts exit `0`.

## Acceptance criteria
- It never executes untrusted embedded scripts.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building AppImages

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
