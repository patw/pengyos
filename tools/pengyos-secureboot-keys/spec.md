# PengyOS secureboot-keys — manage SB keys + sign a UKI

## Summary
Manage PK/KEK/db key material and generate the signing commands for a UKI.

## Purpose
SPEC §1: signed, measured boot we control.

## Deliverable
A Python CLI `pengyos-sbkeys` with `init|enroll-commands|sign` generating sbsign/cert-to-efi-sig-list commands (mockable), never handling keys insecurely.

## Interface
- pengyos-sbkeys init --out keys/
- pengyos-sbkeys enroll-commands --keys keys/ --json
- pengyos-sbkeys sign uki.efi --key keys/DB.key

## Verification (tests are first-class)
- golden commands for enroll/sign.
- wrong key perms refused.
- never prints key material (assert output).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Never leaks or mishandles key material.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Touching firmware

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
