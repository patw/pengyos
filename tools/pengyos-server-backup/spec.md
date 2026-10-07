# PengyOS server-backup — scheduled restic/rustic + a proven restore (US-SRV-4)

## Summary
A backup planner (restic/rustic) that emits a systemd timer + service, plus a restore-test harness that proves a documented restore reproduces a known tree — recovery is verified, not hoped for.

## Purpose
USE-CASES US-SRV-4: a scheduled backup runs; a documented restore to a clean target succeeds; a backup failure alerts.

## Deliverable
A Python CLI `pengyos-backup` with `plan --config backup.yaml --out units/` (emit timer + service + golden restic/rustic argv), `verify-restore` (drive a restore from a fixture repository into a temp dir and check a tree hash), and fail->alert wiring.

## Interface
- pengyos-backup plan --config backup.yaml --out units/
- pengyos-backup verify-restore --repo DIR --target tmp/ [--dry-run]
- pengyos-backup render --config backup.yaml --json

## Verification (tests are first-class)
- golden timer + argv; deterministic schedule.
- restore simulation verifies the expected tree hash; a corrupt snapshot fails.
- retention validation rejects a zero-keep policy.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Restore is proven by a test, not assumed.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Running a real backup
- Cloud credentials

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
