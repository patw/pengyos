# PengyOS recipe-dbus-broker — Buildroot recipe for dbus-broker

## Summary
Our Buildroot recipe + hash for dbus-broker, validated by tests.

## Purpose
SPEC §1: dbus-broker as our system bus.

## Deliverable
A repo with `package/dbus-broker/*` + pytest checks mirroring recipe-uutils.

## Interface
- files: package/dbus-broker/*
- tests: pytest recipe checks

## Verification (tests are first-class)
- version pinned.
- hash present.
- license + depends declared.
- systemd unit installed.

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
