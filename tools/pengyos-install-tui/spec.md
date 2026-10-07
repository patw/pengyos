# PengyOS install-tui — the guided Core/Server installer + unattended --auto

## Summary
A guided text installer for Core/Server over console/serial/SSH: a testable prompt/state model (disk, edition, layout, user, network, confirm) driving pengyos-install, plus a non-interactive `--auto install.toml`.

## Purpose
US-CORE-1 + 'a normal way to install': Core/Server must install unattended (fleet) or guided (a person) without a GUI. The wizard logic must be offline-testable so the loop can verify it without a terminal.

## Deliverable
A Python package `pengyos_install_tui`: `model` (the wizard state machine — steps, validation, back-navigation, derived answers — pure and offline-testable), `run` (render the model on stdin/stdout), and `auto` (parse `install.toml` into the same answer set the interactive path produces).

## Interface
- pengyos-install-tui run [--answers answers.json]
- pengyos-install-tui auto --config install.toml [--json]

### `install.toml` schema
Five fields, each key matching a wizard step (see `pengyos_install_tui.model`):

| field     | accept(s) | notes |
|-----------|-----------|-------|
| `target`  | device path string | `/dev/sdX`, `/dev/nvmeNnM`, … (block-device shape); existence/size injected via `probe` |
| `edition` | `"core"` \| `"server"` | case-insensitive, stored lower-cased |
| `layout`  | `"single-root"` \| `"ab-split"` | case-insensitive |
| `user`    | username string **or** `{username, uid?}` | bare username or mapping |
| `network` | `"dhcp"` \| `"static"` **or** `{mode, address?, gateway?, nameservers?}` | `static` requires `address`; `nameservers` normalised to a list |

A missing required field, an invalid value, or a file that cannot be read/parsed
is a reported error (exit 1), never a crash. Unknown keys are ignored. `auto`
produces the **exact** `Plan` the interactive path produces for the same inputs.

## Verification (tests are first-class)
- the state machine path is deterministic; back-navigation returns to the prior step.
- auto(config) equals the interactive answer set for the same inputs.
- an invalid answer is rejected with a message, not a crash.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- `pengyos-install-tui auto` produces the same plan inputs as the interactive path; the model is fully offline-testable.
- `pytest -q` green; test_github_ready green.

## Non-goals
- The GUI (a separate Rust task)
- Actually running an install (drives pengyos-install; not executed in tests)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
