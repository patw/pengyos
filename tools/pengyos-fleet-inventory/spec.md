# PengyOS fleet-inventory — hardware profiles for the fleet

## Summary
SSH into the home-lab machines, gather hardware facts, and emit declarative profiles (fixture-driven).

## Purpose
SPEC §1.5: the OS morphs around each machine — start by knowing them.

## Deliverable
A Python CLI `pengyos-fleet` that reads a host list, collects hardware facts via an injectable runner, and writes one profile per machine using the hwprofile schema.

## Interface
- pengyos-fleet collect --hosts hosts.toml --out profiles/ [--runner ssh|mock]
- pengyos-fleet diff profiles/

### Host list (`--hosts`)

A TOML document with a top-level `[[hosts]]` array of tables. Each entry:

| field      | required | meaning                                                        |
|------------|----------|----------------------------------------------------------------|
| `name`     | yes      | Logical machine name; also the output directory (`profiles/<name>/profile.toml`). |
| `address`  | yes      | SSH target — a bare host, or `user@host`.                      |
| `fixtures` | no       | Directory of command-output fixtures for the mock runner. Relative paths resolve against the `hosts.toml` file. |

Example (`hosts.example.toml`):

```toml
[[hosts]]
name = "n1"
address = "n1.home.lab"
fixtures = "tests/fixtures/hosts/n1"
```

### Runners (`--runner`)

Fact collection runs through an injectable runner (`pengyos_fleet.runner`):

- `ssh` — `SSHRunner` shells out to `ssh` (`ssh [opts] -- [user@]host command...`).
- `mock` — `MockRunner` returns committed fixture output, one file per command
  named after the command's basename (e.g. `lscpu` -> `<fixtures>/lscpu`).
  Offline and deterministic; the default in tests.

## Verification (tests are first-class)
- fixture command outputs → expected profile.
- mock runner drives the whole flow with no SSH.
- idempotent re-collect makes no changes.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Runs fully offline via the mock runner.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Configuring machines

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
