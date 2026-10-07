# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-22

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, stdlib-only at test time), the
  `pengyos_cosmic_recipes/` package, and the pytest harness.
- `.gitignore`, `README.md`, `CHANGELOG.md`, and `LICENSE` (MIT, Copyright
  (c) 2026 Pat Wendorf) so the repo is GitHub-ready from the first commit.
- `conftest.py` (adds the repo root and the pengyos-cosmic-pkg engine to
  `sys.path` so a bare `python3 -m pytest -q` works without
  `pip install -e .` — the verifier runs exactly that, cwd =
  `tools/pengyos-cosmic-recipes`).
- `tests/test_engine_import.py` — asserts the pengyos-cosmic-pkg engine is
  importable and its public surface is usable (real behavioural checks:
  `is_pinned`, `recipe_symbol`, `license_files`, `parse_manifest`), so a stale
  or missing engine is caught.
- `tests/test_scaffold.py` — package identity, `pyproject.toml` metadata, and
  the four GitHub-ready docs present and non-empty.
- COSMIC component manifest `cosmic.toml` (implementation.md item 2): the core
  group (a bootable session), each component pinned to a concrete reference
  with a declared license and inter-component deps declared by name.
  `cosmic-protocols` (commit on `main`), `libcosmic` `v0.12`, `cosmic-comp` and
  `cosmic-session` (epoch-1.8.0 commit SHAs, since the shared `is_pinned`
  predicate rejects the `epoch-*` tags).
- `pengyos_cosmic_recipes/manifest.py` — loads and validates `cosmic.toml`
  (pinned-tag, license, group and dependency-closure checks), exposes it
  grouped, and expands each component's name-only deps to the target's
  `git_url` / `tag` for the engine's `recipe` builder.
- `pengyos_cosmic_recipes/cli.py` + `__main__.py` — the `pengyos-cosmic-recipes`
  CLI (also `python -m pengyos_cosmic_recipes`); the `manifest` subcommand
  (`--group` / `--json`) implemented, `emit`/`lint` wired as `FeatureNotReady`.
- `[project.scripts]` entry point `pengyos-cosmic-recipes`.
- `tests/test_manifest.py` — the committed manifest, grouping, dep expansion via
  the engine, and every loader validation rule.
- `tests/test_cli.py` — the `manifest` subcommand (human / `--group` / `--json`)
  and the `emit` not-ready report.
- COSMIC component manifest `cosmic.toml` (implementation.md item 2): the core
  group (a bootable session), each component pinned to a concrete reference
  with a declared license and inter-component deps declared by name.
  `cosmic-protocols` (commit on `main`), `libcosmic` `v0.12`, `cosmic-comp` and
  `cosmic-session` (epoch-1.8.0 commit SHAs, since the shared `is_pinned`
  predicate rejects the `epoch-*` tags), all `GPL-3.0-only` / `MPL-2.0` / `MIT`.
- `pengyos_cosmic_recipes/manifest.py` — loads and validates `cosmic.toml`
  (pinned-tag, license, group and dependency-closure checks), exposes it
  grouped, and expands each component's name-only deps to the target's
  `git_url` / `tag` for the engine's `recipe` builder.
- `pengyos_cosmic_recipes/cli.py` + `__main__.py` — the `pengyos-cosmic-recipes`
  CLI (also `python -m pengyos_cosmic_recipes`); the `manifest` subcommand
  (`--group` / `--json`) implemented, `emit`/`lint` wired as `FeatureNotReady`.
- `[project.scripts]` entry point `pengyos-cosmic-recipes`.
- `tests/test_manifest.py` — the committed manifest, grouping, dep expansion via
  the engine, and every loader validation rule.
- `tests/test_cli.py` — the `manifest` subcommand (human / `--group` / `--json`)
  and the `emit` not-ready report.

### Added
- Emit + lint the core-group recipes (implementation.md item 3): the `emit`
  subcommand drives the engine's `recipe` builder once per component in a group
  (`Config.in` / `<pkg>.mk` / `<pkg>.hash`), pinning the manifest's reference and
  license; the `lint` subcommand is the dependency-closure linter, asserting
  every component and every inter-component dependency has a lint-clean recipe.
  Both support `--json`; the CLI no longer reports them as `FeatureNotReady`.
- `pengyos_cosmic_recipes/emit.py` — `emit_group()` expands each component's
  name-only deps to the target's `git_url` / `tag` and emits its recipe; the
  digest seam (`--hash` / placeholder `0`×64) is documented and flagged.
- `pengyos_cosmic_recipes/lint.py` — `closure_lint()` runs the engine's
  `lint_dir` on each recipe and additionally asserts the inter-component dep
  closure, returning a `ClosureReport` (exit 1 on any violation).
- `tests/test_emit.py` and `tests/test_closure_lint.py` — recipe emission
  (three files per component, pinned reference / license / binaries,
  placeholder-vs-supplied digest, determinism, empty-group error) and the
  closure linter (closed when complete, missing-recipe, unclosed-dep,
  lint-violation, empty-root). `tests/test_cli.py` exercises the now-implemented
  `emit`/`lint` subcommands.

### Added
- Author + emit the login group (implementation.md item 4): `cosmic-greeter`,
  the greetd greeter, in the new `login` group. It is pinned to the
  epoch-1.8.0 commit SHA (`ff31b59…`, since the `epoch-*` tag is not pinned),
  ships the `cosmic-greeter` binary, is GPL-3.0-only, and depends on the
  `libcosmic` crate. The `greetd` daemon is a Buildroot package
  (`BR2_PACKAGE_GREETD`) — a non-Rust system dep out of scope for the recipes
  lane, so the closure linter tracks only the inter-component dep (libcosmic).
  The login group closes only once the core group's recipes are present
  (cosmic-greeter → libcosmic).

### Added
- Author + emit the shell group (implementation.md item 5): the ten interactive
  shell components plus `cosmic-settings-daemon` — `cosmic-panel`,
  `cosmic-launcher`, `cosmic-applets`, `cosmic-bg`, `cosmic-randr`,
  `cosmic-settings`, `cosmic-settings-daemon`, `cosmic-osd`,
  `cosmic-notifications`, `cosmic-idle`, `cosmic-icons` — in the new `shell`
  group. Each is pinned to the epoch-1.8.0 commit SHA (the `epoch-*` tag is not
  pinned), ships its cargo binary(ies) (`cosmic-applets` forwards its whole
  applet workspace; `cosmic-icons` is a binary-less icon theme), and declares
  its inter-component deps by name. `cosmic-bg` / `cosmic-randr` are MPL-2.0;
  `cosmic-settings-daemon` / `cosmic-osd` are GPL-3.0-or-later; the rest are
  GPL-3.0-only; `cosmic-icons` is CC-BY-SA-4.0. The shell graph is real and
  cross-referenced (e.g. `cosmic-settings` → cosmic-randr/bg/idle/panel +
  `cosmic-comp`; `cosmic-osd` → cosmic-settings-daemon), so the shell group
  closes only once the core group's recipes are present.

### Added
- Author + emit the apps group (implementation.md item 6): the six COSMIC cargo
  applications — `cosmic-files`, `cosmic-edit`, `cosmic-term`, `cosmic-store`,
  `cosmic-screenshot`, `cosmic-player` — in the new `apps` group. Each is
  pinned to its epoch-1.8.0 commit SHA (the `epoch-*` tag is not pinned), ships
  its cargo binary, is GPL-3.0-only, and declares its inter-component deps by
  name. The app graph is real and cross-referenced: `cosmic-edit` /
  `cosmic-term` build on `cosmic-files`; `cosmic-store` builds on the shell's
  `cosmic-panel`; the rest build on `libcosmic` (+ `cosmic-protocols` for
  `cosmic-files`). `cosmic-screenshot` is a standalone daemon with no
  inter-component dep. The apps group closes only once the core and shell
  recipes are present. `cosmic-workspaces` is deliberately **excluded**: it is a
  JavaScript GNOME Shell extension (no Rust/Cargo, no `is_pinned`-acceptable
  tag), not a cargo component — see the follow-up item. `tests/test_apps.py`
  covers the group (pins, licenses, single-binary forwarding, the dep DAG,
  three-file emission, determinism, and the cross-group closure).

### Added
- Handle `cosmic-workspaces` (implementation.md item 7): verified out-of-band
  (`git ls-remote` + tree + `metadata.json`) that `pop-os/cosmic-workspaces` is a
  JavaScript GNOME Shell extension (`extension.js` / `prefs.js` / `metadata.json`
  with a `shell-version` gate, GPLv3, no `Cargo.toml`, no binary, default branch
  `master_jammy`, no `is_pinned`-acceptable tag) — not a Rust cargo component.
  It is excluded from the cargo recipes manifest and pinned as a stable, tested
  invariant (`tests/test_excluded.py`: not in the manifest, documented in
  `cosmic.toml`, never emitted by any `emit --group`). The non-cargo Buildroot
  recipe / separate handling path is tracked as a follow-up (out of scope for
  the cargo recipes lane).

### Added
- Establish a separate handling path for non-cargo components (implementation.md
  item 9): `pengyos_cosmic_recipes/non_cargo.py` registers the known
  non-cargo components (currently `cosmic-workspaces`, a GNOME Shell extension)
  with the kind of non-cargo recipe it needs and the lane that owns it; the
  recipes lane recognises a non-cargo component by name (`is_non_cargo`,
  `recipe_kind`, `lookup`, `handling_path`) and routes it to its out-of-lane
  handling path. The manifest loader now **rejects** a non-cargo component from
  the cargo manifest (`parse_manifest` raises `ManifestError`), so the
  exclusion is structural, not an accident of absence. A `non_cargo` CLI
  subcommand lists every registered non-cargo component and its handling path
  (human / `--json`). `tests/test_non_cargo.py` covers the registry, the
  recognition helpers, the loader guard (rejects a non-cargo component, allows a
  cargo one), and the CLI. The actual non-cargo Buildroot recipe remains a
  follow-up (out of scope for the cargo recipes lane).

### Added
- Author the non-cargo Buildroot recipe for `cosmic-workspaces` (implementation.md
  item 10): `pengyos_cosmic_recipes/non_cargo_recipe.py` authors the recipe in a
  **separate** non-cargo lane (a GNOME shell-extension *install path*, not a
  cargo package) — `Config.in` / `gnome-shell-extension-cosmic-workspaces.mk` /
  `.hash`. It installs the extension files (`extension.js`, `prefs.js`, the JS
  modules, `metadata.json`, `settings.ui`, `stylesheet.css`) under the GNOME
  shell extension directory keyed by the extension's UUID
  (`cosmic-workspaces@system76.com`, from the real `metadata.json`), installs the
  gschema (`org.gnome.shell.extensions.cosmic-workspaces.gschema.xml`) under glib's
  schema directory (compiled into the cache at target finalisation, matching the
  debian build-dep on `libglib2.0-bin`), and selects `BR2_PACKAGE_LIBGLIB2`. The
  recipe pins the version (`0.1.0`), the license (`GPL-3.0-only`), and references
  the vendored source through the documented `*_SITE` placeholder (the digest
  seam, exactly like the cargo lane). A `non_cargo emit` CLI action (and a
  `non_cargo_recipe.main`) authors it into `--out`; the CLI still lists by
  default. `tests/test_non_cargo_recipe.py` (30 tests) pins the real install
  layout, the digest seam, determinism, lint-cleanliness, the CLI, and that the
  recipe is authored by the non-cargo lane (never the cargo lane). The recipe is
  validated against Buildroot 2026.08's own Kconfig parser (`make oldconfig`
  resolves the symbol cleanly). It is **out of scope for the cargo recipes lane**:
  the cargo manifest, `emit` and closure linter never see `cosmic-workspaces`.

### Added
- Close the dependency loop over the whole manifest (implementation.md item 11):
  the closure linter (`pengyos_cosmic_recipes/lint.py::closure_lint`) now verifies
  the *entire* manifest — all 22 components across core/login/shell/apps — that no
  component and no non-Buildroot (inter-component) dependency lacks a recipe, and
  fails loudly (exit 1, violations to stderr) when it does. Each component is
  checked three ways: it ships `Config.in` + `<pkg>.mk` + `<pkg>.hash`; every
  name-only dep's component also has a recipe (`unclosed-dep`); and each present
  recipe is lint-clean (hash present, reference pinned, license declared, no
  floating git ref). The whole manifest closes cleanly (`22 component(s) have
  lint-clean recipes`, exit 0) once all four groups are emitted, and any single
  missing recipe or unclosed dep is reported with its rule and message. The
  `lint` subcommand (`--manifest` / `--dir` / `--json`) is the CLI gate; the
  `--json` envelope carries `ok`/`components`/`violations` so a consumer can
  dispatch on `ok` alone. This is the "a bootable session + full DE" closure gate.

### Added
- Assert the GitHub-ready contract (implementation.md item 12):
  `tests/test_github_ready.py` pins the four GitHub-ready docs present and
  non-empty (README.md, LICENSE, CHANGELOG.md, `.gitignore`), that LICENSE is
  MIT and carries the canonical `Copyright (c) 2026 Pat Wendorf` line, that
  pyproject declares the MIT license, that CHANGELOG.md is Keep-a-Changelog
  (`## [x.y.z]` header) whose newest version equals the pyproject version, and
  that every relative link in README.md resolves. `test_github_ready` is the
  master gate asserting the whole contract (README, MIT license + copyright,
  CHANGELOG version, `.gitignore`, and — via the green suite — a full green test
  suite) holds together. The repo is GitHub-ready.
