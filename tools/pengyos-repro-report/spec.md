# PengyOS repro-report — rebuild-and-compare report in the witness layout (US-CORE-6)

## Summary
Drive a rebuild-and-compare over two pengyos-build output dirs and emit images/repro-report.json in the exact schema the external witness reads, with a reproducibility score and classified differences.

## Purpose
SPEC 5/556: the artifact must be verifiably reproducible; the witness needs a machine-readable repro result in a known place (today `pengyos-verifier` finds no repro report, so the check fails).

## Deliverable
A Python CLI `pengyos-repro-report` that consumes two build dirs (or two build-manifest.json files), diffs every artifact, classifies each difference (identical / normalized / real), and writes `repro-report.json` (score 0..1) at the conventional witness path.

## Interface
- pengyos-repro-report diff --a DIR_A --b DIR_B --out DIR_A/images/repro-report.json [--json]
- pengyos-repro-report score --report repro-report.json

## Verification (tests are first-class)
- two identical dirs -> score 1.0 and every artifact 'identical'.
- one artifact differing in bytes -> classified 'real', score < 1, nonzero exit for the strict mode.
- schema golden + round-trip; deterministic ordering of artifacts.
- missing manifest / missing dir -> clean error, no traceback.
- unreadable manifest (permissions / a path that is a directory) or an
  unreadable `images/` dir -> a clean `ReproReportError` -> exit 1, no
  traceback (never a silent empty comparison / wrong all-`identical` score).
- malformed manifest or report -> clean error, no traceback.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Reproducibility is reported as a machine-readable score in the witness's path.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Running the build
- Fixing non-determinism (only measuring it)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
