# PengyOS propose-client — route change proposals to the approve gate

## Summary
A client that formats change proposals and submits them to the propose/approve gate (human-gated).

## Purpose
SPEC §3/§1.5: proposals flow to the human, never auto-applied.

## Deliverable
A Python CLI `pengyos-propose` that takes a proposal (from triage/upstream-watch), formats it, and submits via the gate API (mockable).

## Interface
- pengyos-propose submit --proposal p.json [--dry-run]
- pengyos-propose render p.json

## Verification (tests are first-class)
- render golden.
- submit builds correct request (mock).
- Tier 2 without human flag is refused.
- A proposal missing every human-usable field (title, component or tier) is
  refused at load, before render/submit, with a clear CLI error.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Cannot bypass the human gate for Tier 2.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Running the gate server

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
