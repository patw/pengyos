# PengyOS release — publish a signed, versioned release to a local update feed

## Summary
Turn a pengyos-build output into a release: compress the rootfs, collect the UKI + SBOM + attestation + repro report, sign everything, and write a signed metadata index and channel pointer into a static local feed directory — the single artifact both install and update consume.

## Purpose
The update loop's missing BACK half (publish). Buildroot has no package manager, so the release *is* the unit of change; a signed static directory is the whole 'update server' for local testing (decision #4). Without it there is nothing for the updater to fetch.

## Deliverable
A Python package `pengyos_release`: `publish` (build-output dir + edition/version -> a feed tree under `<feed>/pengyos/<edition>/<version>/`), `index` (regenerate + verify the signed metadata index and the `stable`/`testing` channel pointer), `verify` (a feed entry is well-formed, hashed and signed), and `retain` (prune old versions per policy). Deterministic; fixtures; signing via an injectable signer.

## Interface
- pengyos-release publish --build DIR --edition server|core|desktop --version V --feed DIR [--channel stable] [--json]
- pengyos-release index --feed DIR [--json]
- pengyos-release verify --feed DIR [--json]
- pengyos-release retain --feed DIR --keep N [--dry-run]
- pengyos-release prune --site DIR [--apply] [--keep-stable N] [--json]   (the public site: update/ + download/)

## Verification (tests are first-class)
- publish is deterministic (golden feed tree) and self-verifies.
- verify rejects an unsigned entry, a hash mismatch, and a dangling channel pointer.
- retain keeps exactly N and never removes the pointed-at version.
- prune keeps every stable release (or the newest N with --keep-stable) and each edition's newest release; removes a download/<version>/ only when none of its editions (Core: any edition of that version) is kept and it isn't the newest; a dry run changes nothing; --apply rewrites the feed and both index pages; a release without release.json is an error, not a deletion.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A fixture build publishes to a lint-clean, verifiable feed tree; every verify mutation is caught.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Running a real build; HTTPS serving (local static dir only)
- Delta generation (v1 ships full images)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
