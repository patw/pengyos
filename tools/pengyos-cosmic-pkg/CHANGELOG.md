# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Scaffold the repository** — GitHub-ready layout (README, LICENSE (MIT,
  "Copyright (c) 2026 Pat Wendorf"), CHANGELOG, `.gitignore`) and a
  `pyproject.toml` wiring the `pengyos-cosmic-pkg` console script.
- **`pengyos_cosmic_pkg` package** — a standard-library-only Python package with
  a working, tested CLI framework (`pengyos_cosmic_pkg.cli`): an argparse
  front-end registering the three documented subcommands (`vendor`, `recipe`,
  `lint`) with `--manifest`/`--out`/`--dir` + `--json`, a `--version`, clean
  non-zero reporting for a missing manifest and for a subcommand whose feature
  body is not yet landed (`FeatureNotReady`), and a `python -m` entry point.
  The feature bodies (`vendor`/`recipe`/`lint`) are thin, tested placeholders
  that defer to their dedicated subtasks in `implementation.md`.
- **Offline test suite** — `test_github_ready` (the GitHub-ready contract from
  `spec.md`), and `test_cli` (subcommand surface, JSON mode, exit codes, clean
  error handling, `python -m` invocation). Committed fixtures convention under
  `tests/fixtures/` (see `tests/fixtures/README.md`).
- **Component manifest model** (`pengyos_cosmic_pkg.manifest`) — the shared
  input data model for every subcommand: a :class:`ComponentManifest` (name,
  git URL, pinned tag, license, cargo `features`/`binaries`, and `deps`) plus a
  :class:`Dependency` model, a `ManifestError` exception, and a pure
  side-effect-free loader (`load_manifest` / `parse_manifest`). The schema layer
  enforces field presence, string typing, Buildroot-package-name hygiene
  (`[a-z][a-z0-9_-]*`, must start with a letter — a Kconfig symbol may not start
  with a digit) and unique names/deps; the semantic layer enforces git-URL form
  and a **pinned-reference** rule (`is_pinned`: a commit SHA or a version tag,
  never a floating branch/ref). `is_pinned` is the single predicate shared with
  the `lint` subcommand's "no floating git ref" rule, so the two never disagree
  about what "pinned" means. Includes a committed, hand-checkable example at
  `examples/cosmic-text.toml` and 64 hermetic tests.
- **`vendor` engine** (`pengyos_cosmic_pkg.vendor` + `pengyos_cosmic_pkg.cargo`) —
  from a component manifest + the workspace `Cargo.lock`, produce a
  **deterministic**, git-dependency-aware vendored tree: every `git = "..."`
  source is rewritten to `path:VENDOR/<name>`, the crates.io registry source is
  passed through unchanged, each git crate's checked-out source is copied into
  `VENDOR/<name>/`, a `cargo vendor`-style `.cargo/config.toml` is emitted, and
  the source tree is tarred (gzip mtime + uid/gid + modes normalised, sorted
  member order) and hashed to a Buildroot-format `<pkg>.hash`. The `Cargo.lock`
  parser (`cargo.py`) classifies each package as root/registry/git/path and
  parses the `git+…?tag=…#<sha>` source string; `git_slug()` maps a git URL to
  its on-disk repo slug. Driven offline by a committed fixture workspace
  (`tests/fixtures/workspace/`) that ships the git crate checked out on disk.
  17 new hermetic tests (`test_cargo.py`, `test_vendor.py`).

- **`recipe` engine** (`pengyos_cosmic_pkg.recipe`) — emit the Buildroot cargo
  package `package/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash}` in canonical cargo
  form: a `Config.in` Kconfig entry that depends on
  `BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS` and selects `BR2_PACKAGE_HOST_RUSTC`,
  a `.mk` that pins the exact version, names the vendored tarball, declares the
  license (and its files) and forwards the cargo `features`/`binaries`, then
  hands off to `$(eval $(cargo-package))`, plus a one-line `.hash`. The Kconfig
  symbol is `BR2_PACKAGE_<UPPER, dashes->underscores>` (verified against
  `aardvark-dns` -> `BR2_PACKAGE_AARDVARK_DNS`); the SPDX expression is mapped to
  conventional license files (`MIT` -> `LICENSE-MIT`, `Apache-2.0` ->
  `LICENSE-APACHE`, `GPL-*` -> `COPYING`, else `LICENSE`). The recipe pins the
  sha256 from the `vendor` run's `<pkg>.hash` (via the required `--hash-file`;
  `--site` overrides where Buildroot finds the tarball, defaulting to the
  placeholder `file://<VENDORED_SITE>`). Ties `vendor` and `recipe` together: a
  cross-check test confirms the recipe names the tarball the vendored tree
  actually produced and that tarball is self-recognising to Buildroot's cargo
  download post-process (a `VENDOR/` dir nested under one top-level component).
  28 hermetic tests (`test_recipe.py`).

- **`lint` engine** (`pengyos_cosmic_pkg.lint`) — validate a Buildroot package
  directory (`package/<pkg>/`: `Config.in`, `<pkg>.mk`, `<pkg>.hash`) against the
  four reproducible-build invariants: a **`<pkg>.hash` present**, an **explicitly
  pinned `BR2_PACKAGE_<PKG>_VERSION`** (via the shared `manifest.is_pinned`
  predicate, so the recipe and manifest never disagree about "pinned"), a
  **declared license** (`BR2_PACKAGE_<PKG>_LICENSE`), and **no floating git
  `BR2_PACKAGE_<PKG>_SITE`** (a git source URL must carry a pinned commit SHA
  fragment). `lint_dir` returns a `LintReport` whose `is_clean` is the single
  gate a caller dispatches on; the CLI exits `0` when clean and `1` with the
  violations on stderr (or a `{command, ok, dir, violations}` JSON envelope with
  `--json`). A missing package directory is a `FileNotFoundError`. Ties the
  engine together: a fixture `vendor` -> `recipe` run is lint-clean, and each of
  the four violations is reproduced in isolation. 24 hermetic tests
  (`test_lint.py`).

- **GitHub-ready contract, docs-in-sync gate** (`test_github_ready`) — the
  `spec.md` contract now verifies the last unasserted half of the README/LICENSE/
  CHANGELOG/`.gitignore` checklist: the docs are **in sync**.
  `test_github_ready_docs_in_sync` asserts the README's documented subcommand
  surface matches the CLI's implemented subcommand surface **bidirectionally** —
  every subcommand the CLI exposes is documented in the README (nothing ships
  undocumented) and every subcommand the README documents is a real, importable
  feature module with a `main` entry point (nothing is documented that does not
  exist). The CLI parser is the source of truth for what is implemented; the
  README's command table is parsed (first-column backtick identifiers) for what
  is documented, ignoring the unrelated exit-code table. 1 new test; suite 182
  green.

### Fixed

- **`vendor` determinism** — the tarball's `exclude` set only omitted the
  tarball's own basename, so re-running `vendor` into the *same* output dir
  folded the previous `.hash` back into the next tree and changed the hash.
  Both generated outputs (tarball + `.hash`) are now excluded by basename, and
  a `test_vend_is_idempotent_into_same_dir` regression test locks it in.
