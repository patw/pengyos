# PengyOS install-gui — the libcosmic Desktop installer wizard

## Summary
A libcosmic-native graphical installer for PengyOS Desktop that drives pengyos-install: a pure view-model/state machine (cargo-testable with no display) plus the COSMIC screens (disk, layout, user, summary, progress); visuals are a human-ratified artistry domain.

## Purpose
Decision #5: the Desktop needs a GUI wizard in the live session, and libcosmic keeps it in our Rust stack. Long-running: it checkpoints across nights and is expected to take several.

## Deliverable
A Rust workspace `pengyos-install-gui`: a `model` crate holding the whole wizard state machine (steps, validation, back-nav, the derived `install.toml` + layout plan — 100% cargo-testable headless) and a `gui` crate (the libcosmic app) that renders the model and calls the same engine. The model must be usable with no display; the gui crate builds where libcosmic does.

## Interface
- cargo test -p model                      # the state machine, headless
- pengyos-install-gui --answers a.json      # render from a scripted answer set (smoke, headless)

## Verification (tests are first-class)
- the model state machine is deterministic; back-nav and validation are tested headless.
- derived install.toml/layout is golden for a scripted answer set.
- scripted (no-display) mode completes without a window.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- The view-model is fully cargo-testable headless and derives the same plan as the TUI; the libcosmic shell builds and renders each screen from the model.
- `cargo test` green; test_github_ready green.

## Non-goals
- The final visual design (human artistry, Principle 8)
- Running a real install from the GUI in the suite

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
