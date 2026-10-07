# PengyOS cosmic-recipes — Buildroot recipes for the COSMIC components (compositor, shell, apps)

## Summary
The COSMIC component manifest and the emitted Buildroot cargo recipes for the whole DE — cosmic-protocols, libcosmic, cosmic-comp, cosmic-session, the shell (panel/launcher/applets/settings/osd/notifications/bg/icons/idle/randr), cosmic-greeter, and the apps (files/edit/term/store/screenshot/player) — authored via pengyos-cosmic-pkg with a dependency-closure linter. (`cosmic-workspaces` is a JavaScript GNOME Shell extension, not a cargo component, so it is out of scope for the cargo recipes lane.)

## Purpose
US-DSK-1/2/3: the Desktop edition boots to a COSMIC session and ships the COSMIC app set. This is the bulk of the 'get to COSMIC' work (SPEC 2.3); it consumes pengyos-cosmic-pkg so each component is data, not a bespoke port.

## Deliverable
A manifest (cosmic.toml) listing every COSMIC component with pinned tag + license + deps, the emitted Buildroot recipes (via pengyos-cosmic-pkg), and a closure linter asserting every component and every non-Buildroot dependency has a recipe (core group first: a bootable session).

## Interface
- pengyos-cosmic-recipes manifest [--group core|shell|apps] [--json]
- pengyos-cosmic-recipes emit --out DIR [--group core|shell|apps] [--json]
- pengyos-cosmic-recipes lint --dir DIR [--json]

## Verification (tests are first-class)
- closure linter passes a complete manifest and fails one missing a dep recipe.
- emission is deterministic; every component carries a pinned tag + license.
- manifest lint rejects a floating ref or an undeclared license.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Every COSMIC component (core+login+shell+apps) has a deterministic, lint-clean recipe and the closure is complete.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Building COSMIC (operator: network + rust + hours)
- The non-Rust deps (pengyos-cosmic-native-deps)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
