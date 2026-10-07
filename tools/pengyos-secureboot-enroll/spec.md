# PengyOS secureboot-enroll — sign and enrol the Secure Boot chain

## Summary
Manage the Secure Boot trust root: generate/rotate the PK/KEK/db keys, sign the UKI + systemd-boot, produce the enrolment material (MOK or custom-key), and lint that every bootable EFI binary on the ESP is signed by a key that is in db.

## Purpose
Decision #2: Secure Boot is the gate that lets an A/B update be trusted without a human watching. pengyos-sign + pengyos-secureboot-keys exist; nothing signs the whole chain end-to-end or asserts the enrolled policy, so a future update could boot an unsigned binary unnoticed.

## Deliverable
A Python package `pengyos_secureboot_enroll`: `keys` (generate/rotate PK/KEK/db, emit a signed key metadata), `sign-chain` (sign the UKI + bootloader and verify), `enroll` (emit MOK/custom-key enrolment material + instructions), `lint` (every EFI binary referenced by the ESP is signed by a key present in db). Signing runs through an injectable signer (stdlib + an openssl-CLI shim); no root, no real NVRAM.

## Interface
- pengyos-secureboot-enroll keys --out DIR [--rotate] [--json]
- pengyos-secureboot-enroll sign-chain --esp DIR --keys DIR [--json]
- pengyos-secureboot-enroll enroll --keys DIR --mode mok|custom [--json]
- pengyos-secureboot-enroll lint --esp DIR --keys DIR [--json]

## Verification (tests are first-class)
- sign-chain round-trips (sign then verify OK; a tampered binary fails).
- lint rejects an unsigned binary and a binary signed by an untrusted key.
- keys --rotate changes ids and re-signs deterministically.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A signed ESP fixture lints clean and every mutation is caught.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Writing UEFI NVRAM / enrolling on a real machine (operator, human-signed)
- The Microsoft-signed shim path (deferred open question)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
