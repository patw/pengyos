# PengyOS systemd-units — Core unit set + validator

## Summary
The Core systemd unit set and a python validator for unit syntax/sections.

## Purpose
SPEC §2.1 M2: a correct, minimal, verifiable early userspace.

## Deliverable
A repo with `units/*.service|target|socket` and a pytest validator (required sections, ExecStart present where needed, no typos in [Section] names, WantedBy sanity).

## Interface
- files: units/… (journald?, sshd, networkd, resolved, pengy-agent)
- tests: pytest unit validator

## Verification (tests are first-class)
- valid units pass; each rule has a failing fixture (missing ExecStart, bad section name, no [Install]).
- unknown section → warning/fail per rule table.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Validator catches real unit mistakes.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Being systemd-analyze

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
