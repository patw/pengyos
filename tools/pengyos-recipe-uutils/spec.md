# PengyOS recipe-uutils — Buildroot recipe for uutils/coreutils

## Summary
Our own Buildroot recipe + hash for uutils/coreutils, validated by tests.

## Purpose
SPEC §2.1: own the recipes; uutils is the Core coreutils.

## Deliverable
A repo with `package/uutils-coreutils/{Config.in,uutils-coreutils.mk,uutils-coreutils.hash}` and pytest checks (version pinned, hash present, license declared, install steps sane).

## Interface
- files: package/uutils-coreutils/*
- tests: pytest recipe checks (reuse recipe-lint rules)

## Verification (tests are first-class)
- version pinned (no HEAD).
- hash present & 64 hex (verified against the live tarball under `PENGYOS_IT=1`).
- license declared.
- install/symlink lines present; the build selects
  `--no-default-features --features unix`.
- utility list == the pinned `feat_os_unix` fixture (106 names), covering every
  utility GNU coreutils put in Core.
- the external tree's copy of the recipe is byte-identical to this one.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Recipe is lintable and pinned.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building it

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
