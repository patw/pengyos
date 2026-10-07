# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The committed BR2_EXTERNAL recipes** — the repo root *is* a BR2_EXTERNAL
  tree now: `emit`'s output (`package/<dep>/{Config.in,<dep>.mk,<dep>.hash}` for
  greetd / xdg-desktop-portal / cosmic-portal and `package/fonts/Config.in`, plus
  `fragments/cosmic-native.fragment`) is versioned alongside the tool instead of
  only being written to a throwaway temp dir. This is the usable artifact the
  PengyOS build consumes. A new gate (`tests/test_committed_recipes.py`) re-emits
  the committed catalogue into a temp tree and asserts the committed
  `package/` + `fragments/` are **byte-identical** to the fresh emit output, so a
  hand-edited recipe or an `emit` change that is not re-committed fails the build.
  The committed recipes lint clean (`lint_catalogue` / `lint --dir package`).
- **A stronger `test_github_ready` docs-in-sync gate** (`tests/test_github_ready.py`):
  the README now has to prove its documented CLI usage lines are actually valid,
  not merely contain the right words. `test_readme_documented_usage_lines_parse`
  extracts every `pengyos-cosmic-native-deps …` line the README prints, strips the
  optional `[...]` groups and angle-bracket placeholders (`<BR2_EXTERNAL>` → a
  dummy value), drops the leading prog name, and parses the tokens with the real
  `argparse` surface — a documented invocation that raises a usage error (exit 2)
  fails the gate. This closes the one gap the prior bidirectional checks left:
  they proved the README named real subcommands/flags, but not that the exact
  documented invocations the README prints parse. The gate is order-independent of
  the CLI growth (it discovers the documented lines rather than hard-coding them),
  so a new example added to the README is checked too. Runtime stdlib only.

- **The `lint` step** (`pengyos_cosmic_native_deps/lint.py` + `lint` CLI): lints
  the emitted `BR2_EXTERNAL` recipes under `--dir DIR` against the four checks
  the catalogue contract promises — **hash present** (a `sha256  <64-hex>  <source>`
  line, mirroring Buildroot's `check-package` `Hash*` rules), **version pinned**
  (`<PKG>_VERSION` is a concrete release, never `HEAD` / a branch / a URL),
  **license declared** (`<PKG>_LICENSE` non-empty), and **correct infra** (the
  `.mk` ends with a valid `$(eval $(...))` build-system method:
  `meson-package` / `autotools-package` / `generic-package` / `cargo-package`).
  It scans `package/<dep>/` directories, treating a dir with a `<dep>.mk` as
  buildable and a dir with only `Config.in` as data-only (the fonts); every
  recipe must pass or the run exits non-zero (so it gates CI), and `--json`
  writes a deterministic report. The linter is file-format aware (it parses the
  `.mk` / `.hash` shapes `emit` produces) rather than a wrapper around
  `check-package`, so it runs offline in the unit suite and needs no
  `flake8` / `python-magic` / toolchain. Cross-validated against Buildroot's own
  `check-package` (rung **a**): **0 warnings** on all emitted files. Reused in
  tests (`lint_catalogue` proves the committed catalogue lints clean; the four
  checks, the parsers, the runners, the report, and the CLI are all pinned).
  Runtime stdlib only.
- **The hash-verify step** (`pengyos_cosmic_native_deps/verify.py` + `verify`
  CLI subcommand): re-downloads each buildable recipe's upstream tarball from
  `Recipe.download_url` and re-checks the pinned sha256 — the "source + hash"
  rung of the verification ladder (it never compiles). The download seam is
  injectable, so the suite is fully offline: a fixture gzip tarball verifies, a
  hash mismatch reports `mismatch`, and a non-gzip blob (the Cloudflare /
  GitLab sign-in challenge is an HTML page that never starts with the gzip
  magic bytes) reports `challenge_detected` rather than being mistaken for a
  good source. `verify` exits non-zero if any recipe is unverified; `--names`
  selects recipes, `--json` writes a deterministic report. Runtime stdlib only.
  - **The `download_url` field** on `Recipe` (and `data/recipes.json`): the
    exact archive endpoint Buildroot downloads for the pinned source. The repo
    `url` is documentation; the GitHub `/archive/refs/tags` / GitLab
    `/-/archive` archive paths differ from a bare `_SITE` + `_SOURCE`, so it is
    explicit per buildable recipe and required (data-only `none` recipes carry
    no single pinned tarball). `load_catalogue`/`write_catalogue` round-trip it
    and validate a buildable recipe pins one.
  - **Verified against the live upstream tarballs**: every buildable recipe now
    `verified`. `cosmic-portal/epoch-1.8.0` (GitHub, `8b37ce62…`) was already
    proven. `greetd-0.10.3` and `xdg-desktop-portal-1.22.1` were behind a
    Cloudflare challenge on their GitLab `/-/archive` endpoints; the working
    download method is their **canonical GitHub mirrors** — `kennylevinsen/greetd`
    and `flatpak/xdg-desktop-portal` (the upstream moved/mirrored to GitHub).
    The GitHub archives are byte-identical to the pinned tarballs (same commit,
    same sha256: `ee5cb70e…` / `5afeda95…`), so the catalogue's
    `download_url` for both now points at the GitHub archive and `verify` proves
    the pinned sha256 live. The opt-in `PENGYOS_IT=1` integration test pins this.

- **The shipped / recipe-needed classification** (`pengyos_cosmic_native_deps/classify.py`
  + `symbols.py`): each candidate is checked against the vendored 2026.08 symbol
  list and tagged `shipped` (2026.08 already provides it — no recipe) or
  `recipe_needed` (2026.08 does not — a recipe is required). A candidate is
  `shipped` only when *every* symbol it enables is present; the multi-symbol
  `fonts` candidate is `recipe_needed` if *any* of its ten font families is
  absent. The real 2026.08 facts: `greetd`, `xdg-desktop-portal`,
  `cosmic-portal`, and the Fira/Noto `fonts` are **recipe-needed**;
  `libdisplay-info` and `seatd` are **shipped** (already in 2026.08).
  - The machine view is the committed `data/classification.json` (deterministic
    JSON, sorted keys), regenerated by `classify` and pinned by tests.
  - `list` now surfaces the status column; a new `classify [--json] [--symbols
    PATH]` subcommand prints the full per-candidate report.

- **Vendored the authoritative Buildroot `2026.08` kconfig symbol list** as
  committed data at `data/buildroot-2026.08-symbols.txt` (byte-identical copy of
  `pengyos-m2-defconfig`'s snapshot, tag `2026.08`, commit
  `d5180309…`). It includes the 62 `menuconfig` symbols (the load-bearing
  caveat note is preserved) and is the self-contained, offline source of truth
  the follow-up *classify* item checks each candidate against. Tests pin the
  provenance, structural integrity (`sort -u`), and the real present/absent
  facts (greetd / xdg-desktop-portal / cosmic-portal / Fira-Noto absent =
  *recipe needed*; libdisplay-info / seatd present = *shipped*).

- **The recipe catalogue** (`pengyos_cosmic_native_deps/recipes.py`): the
  concrete `BR2_EXTERNAL` recipe metadata for every *recipe-needed* dep, modelled
  as committed data at `data/recipes.json`. Each `Recipe` carries its `name`,
  pinned `version`, upstream `url`, `build_system` (`meson` / `autotools` /
  `make` / `cargo` / `none`), SPDX `license`, download `source`, `sha256` `hash`,
  and the kconfig symbol(s) it enables. `RecipeCatalogue` adds `by_name` /
  `filter_by_names`; `load_catalogue` / `write_catalogue` round-trip the
  deterministic JSON with validation (a buildable recipe must pin a sha256; a
  data-only `none` recipe must not). The `recipes [--json]` CLI subcommand
  surfaces it, and `Recipe.as_dict()` is byte-compatible with the committed data
  file. `cosmic-portal/epoch-1.8.0`'s hash was verified against the live GitHub
  tarball. The catalogue authors exactly the recipe-needed set
  (greetd / xdg-desktop-portal / cosmic-portal / fonts) — no shipped dep gets a
  recipe.

- **The `emit` step** (`pengyos_cosmic_native_deps/emit.py` + `emit` CLI):
  renders each recipe in the catalogue to a `package/<dep>/`
  `Config.in` + `<dep>.mk` + `<dep>.hash` trio (`Config.in` only for a data-only
  `none` recipe such as the fonts) and the `fragments/cosmic-native.fragment`
  that enables every emitted symbol. Rendering is **deterministic** (the same
  catalogue yields byte-identical files on every run, which is what the golden
  tests pin) and idempotent (a re-run over an existing tree overwrites with the
  same bytes). The `.mk` mirrors the Buildroot recipe style — the banner, the
  pinned `_VERSION` / `_SOURCE = $(<PKG>_VERSION).tar.gz` / `_SITE` /
  `_LICENSE` / `_LICENSE_FILES` metadata, and the final `$(eval $(...))` that
  selects the right build-system infrastructure (`meson-package` for
  xdg-desktop-portal, `cargo-package` for greetd / cosmic-portal); the `.hash`
  is a single `sha256  <hash>  <source>` line (two spaces between fields, as
  Buildroot's `check-package` requires); the `Config.in` is keyed off the
  primary symbol with help text indented `<tab><2 spaces>`. `check-package`
  (static rung **a**) reports **0 warnings** on all emitted files. `emit`
  resolves the `Config.in` help line from the candidate catalogue (the recipe
  model carries no description); the `--json` flag prints the deterministic
  emit report. Runtime stdlib only.

## [0.1.0] - 2026-09-21

### Added

- **Scaffold of the `pengyos-cosmic-native-deps` CLI** with `list`, `emit`, and
  `lint` subcommands (argument parsing + dispatch) and a clean error contract
  (`CliError` maps to a one-line `error:` message and exit 1, never a
  traceback). `list` is fully implemented against the candidate catalogue;
  `emit`/`lint` validate their arguments and raise a clean "not implemented"
  error until their follow-up items land.
- **The candidate catalogue** (`pengyos_cosmic_native_deps/catalogue.py`): the
  finite set of native dependencies COSMIC may need (`greetd`,
  `xdg-desktop-portal`, `cosmic-portal`, `libdisplay-info`, `seatd`, the
  Fira/Noto `fonts`), each tagged with the kconfig symbol(s) it enables. The
  shipped/missing classification against the vendored 2026.08 symbol list lands
  in a follow-up item.
- **GitHub-ready project layout**: `README.md`, `LICENSE` (MIT),
  `CHANGELOG.md`, `.gitignore` and `pyproject.toml` (declaring the
  `pengyos-cosmic-native-deps` console script).
- **pytest suite** including `test_github_ready` (docs in sync + green suite)
  and CLI/catalogue smoke tests.
