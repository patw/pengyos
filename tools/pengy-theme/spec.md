# pengy-theme — theme & defaults packager/validator

## Summary
Define, validate, and package a coherent theme + desktop defaults reproducibly.

## Purpose
SPEC §2.3 / Principle 8: human artistry, made reproducible.

## Deliverable
A Rust CLI + lib `pengy-theme` that validates a theme manifest, checks contrast/consistency, and emits installable artifacts + a hash.

## Interface
- pengy-theme validate theme.toml
- pengy-theme pack theme.toml --out dist/
- pengy-theme contrast fg bg

## Verification (tests are first-class)
- schema validation errors.
- contrast math against known pairs.
- pack determinism (two runs identical).
- pack output is a **valid POSIX *ustar*** archive: every 512-byte header block
  has the canonical field offsets and a checksum GNU `tar` accepts, and the
  archive lists/extracts cleanly under `tar -tf` / `tar -xf`. This is opt-in
  (`PENGYOS_IT=1`) since it needs the system `tar` binary; the default suite
  validates the layout in-repo.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Deterministic packaging.
- `cargo test` green.

## Non-goals
- Rendering previews

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
