# PengyOS cosmic-pkg — the COSMIC packaging engine (git Rust workspace -> Buildroot package)

## Summary
A tool that turns a git Rust component (URL + pinned tag + Cargo.lock) into a Buildroot cargo package with vendored crates — git-dependency aware — emitting Config.in/<pkg>.mk/<pkg>.hash plus a lint, so the ~20-crate COSMIC stack can be packaged from source without hand-writing each recipe.

## Purpose
SPEC 2.3/6.9 + the decision to adopt COSMIC: Buildroot 2026.08 has cargo infra but not the cosmic-* packages, and COSMIC's workspace pulls crates from git (libcosmic, smithay) which Buildroot's crates.io-shaped infra does not vendored directly. Without this keystone every recipe is a bespoke port; with it, each component is data.

## Deliverable
A Python package `pengyos_cosmic_pkg` with `vendor` (deterministic vendored source tree from a component manifest + Cargo.lock, rewriting git deps to the vendored path), `recipe` (emit package/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash} in Buildroot cargo form, version+license pinned), and `lint` (hash present, version pinned, license declared, no floating refs). Hermetically tested against a small committed fixture git-workspace (no network).

## Interface
- pengyos-cosmic-pkg vendor --manifest m.toml --cargo-lock Cargo.lock --out DIR [--json]
  (the git-sources mirror defaults to `<--cargo-lock dir>/git-sources`: the
  on-disk mirror of checked-out git crates the engine copies into `VENDOR/`
  instead of cloning).
- pengyos-cosmic-pkg recipe --manifest m.toml --out DIR [--json]
- pengyos-cosmic-pkg lint --dir DIR [--json]

## Verification (tests are first-class)
- vendoring is deterministic and rewrites the fixture's git dep to the vendored path (golden).
- recipe emission is golden for the fixture component.
- lint rejects each of: unpinned version, missing hash, undeclared license, floating git ref.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- A fixture git-workspace yields a deterministic vendored tree + a lint-clean Buildroot recipe.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Compiling Rust or running Buildroot (operator work)
- Downloading real COSMIC sources at test time (fixtures only)

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
