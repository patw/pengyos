# PengyOS debx — bounded deb/rpm extractor (never runs scripts)

## Summary
Extract a deb/rpm into an isolated prefix, analyse DT_NEEDED, report missing libs; never execute maintainer scripts.

## Purpose
SPEC §2.5: the extractor is a bounded convenience with sharp edges, not a compatibility guarantee.

## Deliverable
A Python CLI `pengyos-debx` with `extract|analyse|install-prefix`, plus an explicit refusal to run maintainer scripts.

## Interface
- pengyos-debx extract pkg.deb --into /opt/app
- pengyos-debx analyse /opt/app [--libdirs ...]
- pengyos-debx list-scripts pkg.deb

## Verification (tests are first-class)
- fixture .deb (built in-test with `ar`+tar) extracts to expected tree.
- maintainer scripts are listed but never executed (poison script would create a file — assert it doesn't).
- ELF DT_NEEDED of a fixture .so is parsed correctly.
- missing-lib reporting against a libdir fixture.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Never executes package scripts; uses pure ELF parsing (no ldd).
- `pytest -q` green; test_github_ready green.

## Non-goals
- Resolving dependencies
- Making debs 'just work'

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
