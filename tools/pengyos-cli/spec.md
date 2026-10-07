# pengyos — the meta-CLI

## Summary
One entry point over the PengyOS tools: build, verify, triage, profile, sbom…

## Purpose
SPEC §2: a coherent, ownable toolchain surface.

## Deliverable
A Rust binary `pengyos` that dispatches to subcommands (shelling out to the Python tools where appropriate) with a consistent UI and config.

## Interface
- pengyos build <target> | verify <image> | triage | profile | sbom <artifact> | doctor

## Verification (tests are first-class)
- argv/dispatch golden per subcommand (mock runner).
- doctor readiness matrix from a fixture host description.
- --json schema stable.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Consistent, scriptable surface.
- `cargo test` green.

## Non-goals
- Reimplementing the Python tools in Rust

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
