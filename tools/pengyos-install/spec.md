# PengyOS install — the edition-agnostic install engine (install = first update)

## Summary
One engine that lays a signed release onto a blank disk: (release, target, layout) -> an ordered plan -> execute via an injectable runner, writing the rootfs to slot A, the UKI to the ESP, the loader entries, the data partition, and first-boot provisioning — the same artifact the updater consumes.

## Purpose
Decision #3: unify install with update. Generalizes the Core-only `pengyos-installer` (which copies the running stick) into a release-driven, edition-agnostic engine any front-end (TUI/GUI/auto) drives.

## Deliverable
A Python package `pengyos_install`: `plan` (pure: release manifest + layout -> ordered steps, every step a shell-quotable argv; golden), `install` (execute through an injectable runner; refuse without `--yes`; re-check the target each run; tear down — unmount/sync — on success and on failure), and `provision-plan` (the first-boot steps: hostname, users, SSH keys, network, from `install.toml`).

## Interface
- pengyos-install plan --release rel.json --layout plan.json [--config install.toml] [--json]
- pengyos-install install --release rel.json --layout plan.json [--config install.toml] --yes [--runner CMD]
- pengyos-install provision-plan --config install.toml [--layout layout.json] [--slot a] [--json]

## Verification (tests are first-class)
- plan is deterministic/golden for a fixture release+layout.
- install refuses without --yes; a required-step failure aborts and unmounts everything it mounted.
- provision-plan content (fstab/hostname/ssh) is golden.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A fixture release installs to a fake target end-to-end through the runner, with correct teardown on failure.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Writing to a real device at test time
- The wizard UX (separate TUI/GUI tasks)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
