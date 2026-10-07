# PengyOS guest — declarative distrobox/podman guests

## Summary
Manage compatibility guest environments (Lane 4) declaratively: create/enter/export/teardown, no host writes.

## Purpose
SPEC §2.5 Lane 4: give a deb/rpm-only app the distro it expects, in a box.

## Deliverable
A Python CLI `pengyos-guest` driven by `guests.toml`, wrapping podman/distrobox with a mockable runner.

## Interface
- pengyos-guest apply guests.toml | enter <name> | export <name> --app X | destroy <name> | list

## Verification (tests are first-class)
- apply is idempotent; argv golden for create/provision.
- export produces a host launcher referencing the guest.
- no host paths outside the declared set appear in argv.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Declarative + idempotent.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building images
- Being a container runtime

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
