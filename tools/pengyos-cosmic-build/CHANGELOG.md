# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Scaffold the repository** — GitHub-ready layout (README, LICENSE (MIT,
  "Copyright (c) 2026 Pat Wendorf"), CHANGELOG, `.gitignore`) and a
  `pyproject.toml` wiring the `pengyos-cosmic-build` console script.
- **`pengyos_cosmic_build` package** — a standard-library-only Python package
  with a working, tested CLI framework (`pengyos_cosmic_build.cli`): an argparse
  front-end registering the four documented subcommands (`vendor-all`, `emit`,
  `wire`, `lint`) with `--manifest`/`--sources`/`--dl`/`--vendor`/`--out`/`--repo`
  + `--json`, a `--version`, clean non-zero reporting for a missing input and
  for a subcommand whose feature body is not yet landed (`FeatureNotReady`), and
  a `python -m` entry point. The feature bodies (`vendor_all`/`emit`/`wire`/
  `lint`) are thin, tested placeholders that defer to their dedicated subtasks
  in `implementation.md`.
- **Offline test suite** — `test_github_ready` (the GitHub-ready contract from
  `spec.md`, including the bidirectional docs-in-sync gate) and `test_cli`
  (subcommand surface, JSON mode, exit codes, clean error handling,
  `python -m` invocation). Committed fixtures under `tests/fixtures/`.

- **Wiring data model** (`pengyos_cosmic_build.records`) — the two things the
  other lanes need, modelled as data: :class:`VendorRecord` (the per-component
  vendored-source record: source tarball name, sha256, and real SITE, plus name
  + version) with a strict :meth:`~VendorRecord.validate` (a Buildroot package
  name, a 64-hex sha256, a non-empty SITE that is never the
  `file://<VENDORED_SITE>` placeholder), and JSON round-trip
  load/write/:func:`load_records` (sorted, reported-not-crashed on malformed).
  :func:`symbol_for` is the single ``BR2_PACKAGE_<UPPER, dashes->underscores>``
  transform the emitted recipe and the desktop defconfig must agree on — pinned
  against the real `pengyos_desktop_bringup_defconfig` and cross-checked against
  the `pengyos-cosmic-pkg` engine's own `recipe_symbol`, so the two can never
  silently diverge about which package a symbol names. 22 tests.

- **`vendor-all` engine** (`pengyos_cosmic_build.vendor_all`) — drive the
  `pengyos-cosmic-pkg` vendoring engine per component through an **injectable
  runner** (:class:`VendEngineRunner` for the real engine, :class:`FakeVendorRunner`
  in tests). For each component it locates the checked-out sources + its
  `Cargo.lock`, runs `pengyos_cosmic_pkg.vendor.vend` with the component's
  `git-sources` mirror, and captures the deterministic tarball (wrapped in a
  single `<name>-<version>/` dir so Buildroot's cargo post-process recognises
  it as already-vendored), its sha256, and a real `file://<dl>/<name>/` SITE.
  Each outcome is written as a `VendorRecord` (`<name>.json`) under `--out`; a
  component missing its `Cargo.lock` raises :class:`VendorAllError` (reported,
  exit 1, never a traceback). The CLI (`vendor-all --manifest --sources --dl
  --out [--json]`) was wired end-to-end and verified against the real engine.
  12 tests (`test_vendor_all.py`).

- **`emit` engine** (`pengyos_cosmic_build.emit`) — the recipes lane's missing
  half: joins the manifest with the `vendor-all` records and renders, for every
  component in a `--group`, the three Buildroot recipe files
  (`package/<pkg>/Config.in`, `<pkg>.mk`, `<pkg>.hash`) pinning the **real**
  digest + SITE (never the `file://<VENDORED_SITE>` placeholder — a placeholder
  record raises `EmitError`) and keyed off `symbol_for(pkg)` (the defconfig's
  `BR2_PACKAGE_*` convention). The Make var prefix is `<UPPER_NAME>`
  (`COSMIC_COMP_*`), not the `BR2_PACKAGE_` symbol. `render_config_in` is
  **check-package-clean** (tab-indented `help`, `<tab><2 spaces>` help body,
  truly-empty blank lines, no trailing blank line) — the cheapest Buildroot
  verification rung, verified against the real `utils/check-package` with 0
  warnings. `emit_group()` reports skipped components (`missing_records`) but
  `main()` enforces completeness (exit 1 on an incomplete emit); the CLI catches
  the new `EmitError` (a `RuntimeError`) so a bad record is a clean exit 1, never
  a traceback. 19 tests (`test_emit.py`), incl. an opt-in `PENGYOS_IT=1`
  `check-package` integration test.

- **`wire` engine** (`pengyos_cosmic_build.wire`) — the assembly step that closes
  the gap between the emitted recipes and a buildable `BR2_EXTERNAL` tree:
  installs the emitted COSMIC cargo recipes + the sibling
  `pengyos-cosmic-native-deps` recipes into the tree's `package/` dir, installs
  the desktop defconfig(s) + board overlay(s), wires the tree's `Config.in`
  (one `source` line per installed package, idempotently), and registers the
  `pengyos-desktop` target in `targets.toml` (idempotently). The `Config.in`
  wiring (`add_config_in_sources`) owns a single dedicated section: a rewrite
  keeps the opener comment, the source lines that already live *inside* that
  section (packages added in a prior run), then the genuinely-new wanted lines —
  a package already sourced *elsewhere* (a native dep) is left where it is, so a
  recipe is never `source`d twice (which would redefine its kconfig `config`
  entries and fail the Buildroot parse). Every step is idempotent. The CLI
  registers the target **only** with an explicit `--targets PATH` — a bare
  `wire --repo --sources` (or `--targets ""`) skips registration and never
  mutates a real sibling `targets.toml`. 23 tests (`test_wire.py`).

- **`lint` engine** (`pengyos_cosmic_build.lint`) — the buildable-shaped gate
  that closes the gap the recipes lane leaves open. :func:`lint_repo` runs four
  checks and concatenates their issues so a single run reports every class of
  breakage at once: **no placeholder SITE/hash** (a recipe that still pins the
  `file://<VENDORED_SITE>` SITE, or a buildable recipe whose `.hash` is missing
  or carries a non-64-hex digest — data-only recipes like the fonts carry no
  hash and are skipped), **every defconfig symbol resolves** (a `BR2_PACKAGE_*`
  the defconfig references must be enabled by a recipe in the tree *or* exist in
  the vendored Buildroot `2026.08` symbol list — a core package PengyOS gets for
  free; the canonical unbuildable case is `BR2_PACKAGE_COSMIC_PORTAL`, which the
  bringup defconfig references but which no recipe enables and which is not a
  2026.08 core symbol), **native deps present** (a native dep the defconfig
  references, from the committed `pengyos-cosmic-native-deps` catalogue, must be
  installed as a recipe — this *owns* the native-dep symbols so a missing dep is
  reported once as `missing_native_dep`, not double-counted as unresolved), and
  **target registered** (the `pengyos-desktop` target in `targets.toml`; a
  missing file is vacuous, not a violation — the tree may be wired without a
  target). The 2026.08 symbol list and the native-dep catalogue are imported
  lazily, so a bare `python -m pengyos_cosmic_build` works on a bare checkout.
  The CLI (`lint --repo [--json]`) returns exit 0 for a clean tree, 1 for a
  violating one (a human table on stderr or the `{command, ok:false, issues}`
  JSON envelope). 19 tests (`test_lint.py`), incl. an opt-in `PENGYOS_IT=1`
  emit + wire + lint pipeline.

- **`test_github_ready`** (`tests/test_github_ready.py`) — asserts the
  GitHub-ready contract from `spec.md` (README, LICENSE, CHANGELOG, `.gitignore`,
  the `pengyos-cosmic-build` console script + `requires-python`, the
  `python -m` smoke run, and the bidirectional docs-in-sync gate). The LICENSE
  check was **strengthened**: the original `"MIT" in text` assertion was too
  weak — "MIT" is a substring of the uppercase "LIMITED"/"LIMITATION" boilerplate
  that appears in *many* license texts, so it passed for a non-MIT license too.
  It now verifies the distinctive canonical MIT preamble
  (`"Permission is hereby granted, free of charge, to any person obtaining a
  copy"`) plus the "Copyright (c) 2026 Pat Wendorf" holder, via an extracted
  `_is_mit_license()` helper, and a new mutation-guard test pins that an
  Apache-2.0-style license (same holder, different license) is rejected.
  5 tests (`test_github_ready.py`).
