# PengyOS media — build the live install medium for an edition

## Summary
Assemble a bootable install medium (ISO or disk image) from an edition release: the live/installer rootfs + the installer + a boot entry, as an explicit plan plus an injectable executor — reproducible, not the hand-made stick.

## Purpose
There is no media builder today; the 'live stick' is hand-made. A reproducible medium is the front door of 'a totally normal way to install PengyOS'.

## Deliverable
A Python package `pengyos_media`: `plan` (edition + release -> the medium's layout: ESP, live squashfs/rootfs, the xorriso/mkfs/squashfs argv), `build` (drive the injectable runner; `--dry-run` renders argv), and `lint` (the medium carries the installer, a boot entry, the release, and an os-release that names the edition).

## Interface
- pengyos-media plan --edition desktop --release rel.json --out media.json [--json]
- pengyos-media build --plan media.json [--dry-run|--commit] [--runner CMD]
- pengyos-media lint --plan media.json [--json]

Notes: `plan --out` is optional — omit it to print the plan to stdout (with
`--json` for the machine-readable form, otherwise a human-readable summary).
The emitted plan is the serialisable `MediumPlan` (see
`pengyos_media/model.py`); `build`/`lint` consume exactly what `plan` produced.

## Verification (tests are first-class)
- plan is deterministic/golden; build --dry-run renders argv and runs nothing.
- lint rejects a medium missing the installer / boot entry / release.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A fixture release yields a lint-clean, golden medium plan; dry-run is side-effect free.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Running real xorriso/mksquashfs in the test suite
- Signing the medium (reuse pengyos-sign)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
