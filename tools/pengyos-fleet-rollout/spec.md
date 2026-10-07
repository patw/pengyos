# PengyOS fleet-rollout — plan, stage and roll back an image/update rollout across the fleet

## Summary
Turn a built PengyOS image (or a pending update set) plus a `pengyos-fleet-inventory` into a staged rollout plan (batch order, per-batch health gate, per-host rollback record), apply it through an injectable transport (no-op/dry-run by default), and prove `rollback` restores the recorded prior artifact — as declarative data.

## Purpose
Cross-cutting (README self-hosting + fleet ops): `pengyos-fleet-inventory` *describes* the fleet, but nothing plans or reverses a rollout. Shipping one bad image to every machine with no way back is the failure mode this closes, and it composes with `pengyos-mutations` (journaling) and `pengyos-tiers` (gating).

## Deliverable
A Python package `pengyos_fleet_rollout` with `plan` (image id + inventory -> ordered batches with health gates and a rollback record), `apply` (drive an injectable transport per host; dry-run unless `--commit`), and `rollback` (re-apply every recorded prior artifact in reverse), hermetically tested with a fake transport + a committed fake inventory + a simulated partial failure.

## Interface
- pengyos-fleet-rollout plan --image IMG --inventory inv.toml [--batch-size N] [--json]
- pengyos-fleet-rollout apply --plan plan.json [--transport CMD] [--dry-run|--commit]
- pengyos-fleet-rollout rollback --journal journal.json [--dry-run|--commit]

## Verification (tests are first-class)
- plan is deterministic and puts the canary batch first; empty inventory is an error.
- apply --dry-run renders argv and performs no transport call.
- a mid-rollout gate failure halts the rollout and the journal names the untouched hosts.
- rollback restores exactly the hosts that were applied, in reverse, and is idempotent.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A simulated partial rollout is fully reversed by `rollback` (every touched host back to its prior artifact).
- `pytest -q` green; test_github_ready green.

## Non-goals
- Touching a real machine at test time (injectable transport + fixtures only)
- Building the image (consumes a built artifact; `pengyos-build` produces it)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
