# PengyOS measured-boot — verify TPM2 PCRs from a swtpm boot

## Summary
Boot under a software TPM (swtpm), read PCRs, and verify them against an expected event log.

## Purpose
SPEC §1: measured boot / attestation, testable without hardware.

## Deliverable
A Python CLI `pengyos-measured-boot` that (with a mockable swtpm) boots an image, extracts PCR values, and compares to expected values derived from an event-log policy.

## Interface
- pengyos-measured-boot run --image IMG --expect pcr7=0x… [--json OUT]
- pengyos-measured-boot verify --pcrs pcrs.json --policy policy.toml

## Verification (tests are first-class)
- policy pass on good PCRs.
- tampered PCR → fail naming the PCR.
- swtpm runner argv golden.
- no real swtpm needed.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Mismatch is specific and actionable.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Securing the TPM
- Real hardware attestation

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
