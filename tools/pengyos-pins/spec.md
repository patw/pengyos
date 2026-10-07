# PengyOS pins — the pinned component manifest

## Summary
A single source of truth for pinned components (version, upstream, hash, license) that generates Buildroot config and reports.

## Purpose
SPEC §2.1: recipes are our data; pins are how we track them.

## Deliverable
A Python tool `pengyos-pins` that validates `pins.toml`, emits a Buildroot fragment + a human report, and detects drift against a lockfile.

## Interface
- pengyos-pins validate pins.toml | emit --format buildroot-fragment|report|json | lock [--lock pins.lock.toml] | drift --lock pins.lock.toml

The **lockfile** (`pins.lock.toml`, default) is a committed snapshot of the
resolved components (version + sha256, plus the full component record). `lock`
writes it from a validated manifest; `drift --lock <path>` diffs the working
`pins.toml` against it and reports added / removed / version-changed /
hash-changed entries (exit 1 when drift is detected, so it doubles as a CI
guard).

## Verification (tests are first-class)
- valid pins file passes; each validation rule has a failing fixture.
- emit is deterministic and re-parses.
- drift detects added/removed/version-changed/hash-changed entries.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Schema + validation are documented and enforced.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Fetching sources
- Resolving dependencies

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
