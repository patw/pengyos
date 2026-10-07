# PengyOS layout — the declarative disk/layout planner (ESP + A/B + data, optional LUKS)

## Summary
Compute an explicit, deterministic disk layout for an edition install: GPT with ESP + root-A + root-B + data (btrfs), sized from the release and the target disk, with optional LUKS2 and swap — as data the install engine executes.

## Purpose
Decision #1: A/B + btrfs. The install engine and the image must agree on ONE layout definition; today the installer hardcodes GPT + ESP + ext4 and there is no shared planner.

## Deliverable
A Python package `pengyos_layout`: `plan` (a target probe + edition + options -> ordered partitions + filesystems + mountpoints, golden), `validate` (sizes vs disk, GPT alignment, ESP bounds, A/B slot symmetry, refuse the live device), and `emit` (the sgdisk/mkfs/mount argv for the executor).

## Interface
- pengyos-layout plan --target PROBE.json --edition server --out plan.json [--json]
- pengyos-layout validate --plan plan.json [--json]
- pengyos-layout emit --plan plan.json [--json]

## Verification (tests are first-class)
- plan is deterministic/golden; A/B slots are byte-symmetric in size.
- validate rejects each invariant violation (too small / overlap / asymmetric / live device).
- emit produces shell-quotable argv for every step.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A fixture probe yields a lint-clean, golden layout plan; every invariant has a firing test.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Actually partitioning a disk (the engine executes; tests never touch a device)
- Encryption key management policy (a later desktop task)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
