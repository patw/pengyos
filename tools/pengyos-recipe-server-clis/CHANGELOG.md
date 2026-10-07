# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep-a-Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased
### Added
- **Installable CLI / github-ready entry point** (iter 6): the README / spec
  "Interface" documented `pengyos-recipe-server-clis list [--json]`,
  `emit --out DIR [--json]`, `lint --dir DIR [--json]`, but the repo declared no
  console-script entry point — the documented command silently did not exist
  after `pip install .` (and `python -m pengyos_recipe_server_clis` failed with
  "No module named __main__"). Wired it up: added `[project.scripts]`
  `pengyos-recipe-server-clis = "pengyos_recipe_server_clis.emit:main"` to
  `pyproject.toml` (the CLI implementation already lives in `emit.py`'s
  `build_parser` / `main`) and a `pengyos_recipe_server_clis/__main__.py` so the
  package runs both installed and via `-m`. Extended the `test_github_ready`
  contract to assert, in addition to the four metadata files + green suite, that
  (1) `pyproject.toml` declares the console script and targets
  `pengyos_recipe_server_clis.emit:main`, (2) that target imports cleanly and is
  callable, and (3) the documented CLI actually runs end-to-end —
  `python -m pengyos_recipe_server_clis emit --out <tmp>` then
  `lint --dir <tmp>` exits 0 over the six emitted recipes. This closes the
  docs-in-sync gap the scaffold left open. `pytest -q` green (184 passed, 1
  opt-in skipped).
- **Buildroot recipe linter** (iter 5): `pengyos_recipe_server_clis/lint.py` —
  the *reader* half of the emitter's contract. Parses an emitted `<tool>.mk`
  and cross-checks it against the sibling `<tool>.hash` and the Buildroot recipe
  grammar via five rules (`version_pinned`, `hash_present`, `license_declared`,
  `license_file_declared`, `source_url_present`): a concrete pinned version (never
  `latest` / `HEAD` / `$(…)`), a `.hash` whose `sha256` covers the pinned tarball,
  a non-empty `_LICENSE`, a non-empty `_LICENSE_FILES`, and an absolute `https`
  source (or a `$(call github, …)` download helper). `parse_recipe` / `lint_tool`
  / `lint_dir` / `lint_anything` are pure and stdlib-only so the rules are reused
  directly in tests; `discover_tool_dirs` accepts the emit output root, the
  `package/` dir, or a single `<tool>` dir. The `lint` subcommand is wired into
  the top-level CLI (`pengyos-recipe-server-clis lint --dir DIR [--json]`) via a
  passthrough that delegates to lint.py — a single source of truth for the rule
  set. `pytest -q` green (181 passed, 1 opt-in skipped); the suite rejects one
  mutation per rule (missing `.hash`, floating `latest`, undeclared license) and
  asserts the emitted recipes are lint-clean. The emitter's recipe shape is also
  `check-package`-clean (blank line after the banner fence; `Config.in` help text
  word-wrapped to ≤62 chars after the `<tab><2 spaces>` indent, mirroring the
  fzf / starship reference recipes).
- **Buildroot external fragment** (iter 4): `fragments/server-clis.fragment` —
  emitted by the emitter (`emit.render_fragment` / `emit.emit_fragment`, written
  by `emit --out DIR` to `<out>/fragments/`). A fixed header block followed by
  one `BR2_PACKAGE_<TOOL>=y` line per tool in committed catalogue order, each
  line exactly matching the symbol that tool's emitted `Config.in` declares — so
  the promised tool set and the enabled symbol set can never drift (no more, no
  less). `parse_fragment` is its inverse (skips comments/blanks, rejects
  malformed/non-`=y` symbols). `pytest -q` green (143 passed, 1 opt-in skipped);
  the suite includes a golden (byte-for-byte) fixture and cross-checks every
  fragment symbol against the emitted `Config.in` symbol.
- **Buildroot recipe emitter** (iter 3): `pengyos_recipe_server_clis/emit.py` —
  the single source of truth for the recipe *shape*. For each tool it renders
  `package/<tool>/{Config.in, <tool>.mk, <tool>.hash}` deterministically from the
  committed catalogue, mirroring the `fzf` / `starship` recipe shape in
  `pengyos-server-toolset`:
  - **Build-system families** drive the two things that differ per tool — the
    `$(eval $(<macro>))` build-macro (`cargo-package` for the Rust tools
    zoxide/sd/helix, `golang-package` for the Go tool node_exporter,
    `cmake-package` for the C/C++ tools btop/neovim) and the host-toolchain
    `depends on …_TARGET_ARCH_SUPPORTS` / `select BR2_PACKAGE_HOST_*` lines
    (present for cargo/golang, absent for cmake). The family table is data, not
    inference — `zoxide` is Rust (Buildroot itself ships it as a
    `cargo-package`), the one surprise.
  - `Config.in` uses a hidden `config` symbol with a `bool` prompt (no
    `menuconfig` — these CLIs expose no sub-options); the help block carries the
    upstream URL.
  - `.mk` pins the version, sets `PKG_SITE = $(call github,…)` with the exact
    `site_version` (the `v` prefix is spliced via `Tool.tag_prefix`; `helix`
    carries none), records the license + license file, and evaluates the macro.
  - `.hash` carries the committed source sha256 of the pinned tarball.
  - A CLI: `list` / `emit --out DIR` (plus `--json`). `pytest -q` green (127
    passed, 1 opt-in skipped); the suite includes golden (byte-for-byte against
    committed fixtures), determinism, and structural-teeth (independent macro /
    symbol / host-dep / hash assertions) tests. The emitted recipes also parse
    cleanly under Buildroot's already-built `conf` kconfig frontend.
- **Tool catalogue as committed data** (iter 2): `pengyos_recipe_server_clis/catalogue.py`
  plus `pengyos_recipe_server_clis/data/catalogue.json`, the authoritative
  release metadata for the six Server comfort CLIs. Each entry records the
  upstream `name`, `repo`, pinned `version`, `tag`, upstream `homepage` URL,
  SPDX `license`, the license file inside the release tarball, and the source
  `sha256` of the pinned release tarball — fetched from the live release API and
  release tarballs (verified with an opt-in `PENGYOS_IT=1` integration test).
  Derived views (`source`, `homepage`, `tag_prefix`, `site_version`) are
  deterministic; `helix` is the one tool whose tags carry no `v` prefix.
  `pytest -q` green (54 passed, 1 opt-in skipped).
- Repository scaffold: `pyproject.toml`, the `pengyos_recipe_server_clis/`
  package, a pytest harness (`tests/` with `conftest.py`, `test_scaffold.py`,
  `test_github_ready.py`), and GitHub-ready metadata (`README.md`, `LICENSE`
  (MIT, Copyright (c) 2026 Pat Wendorf), `CHANGELOG.md`, `.gitignore`).
  `pytest -q` green (13 passed).

## [0.1.0] — 2026-09-22
### Added
- Repository scaffold for PengyOS recipe-server-clis — Buildroot recipes for the
  Server comfort CLIs (zoxide, sd, node_exporter, btop, helix, neovim).
