# PengyOS tiers — the 3-tier change policy engine

## Summary
Classify a proposed change into Tier 0 (image), Tier 1 (agent-mutable), or Tier 2 (human-signed) and gate it.

## Purpose
SPEC §1.5/§9: the AI drafts anything; nothing crosses the security boundary without a human.

## Deliverable
A Python library + CLI `pengyos-tiers` that, given a change description (paths, action, target), returns the tier + required approvals, from a declarative rules file.

## Interface
- pengyos-tiers classify --action modify --path /etc/foo --json
- pengyos-tiers check --rules tiers.toml <change.json>

## Verification (tests are first-class)
- /etc config change → T1; bootloader binary/keys → T2; kernel package → T2; unknown path → T2 (default-deny).
- rules precedence + specificity.
- CLI exit code reflects 'needs human'.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Default-deny is proven by a test.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Enforcing at the kernel level

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
