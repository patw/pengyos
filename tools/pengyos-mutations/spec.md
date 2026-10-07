# PengyOS mutations — journaled, reversible system changes

## Summary
Snapshot → apply → record → revert; converge live fixes into declarative profile entries.

## Purpose
SPEC §1.5: Pengy mutates freely, but every change is snapshotted, attested, revertible, and learned.

## Deliverable
A Python CLI `pengyos-mutations` that runs a change under a pluggable backend (mock/snapshot), records it in the attest log, supports revert, and emits a converging profile patch.

## Interface
- pengyos-mutations apply --desc S -- cmd… | revert <id> | log [--json] | converge <id> --out profile.patch.toml

## Verification (tests are first-class)
- apply records an entry and a snapshot; revert restores the tracked file.
- a failing command still records the attempt and leaves revert available.
- converge produces a patch for a file-config change fixture.
- no privilege required (mock backend).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Every mutation is reversible and recorded.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Actually managing btrfs
- Privileged operations

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
