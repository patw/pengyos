# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-25

### Removed
- `package/pengy/pengy-web.service`, and the tests that asserted its shape
  (`tests/test_service_and_config.py` → `tests/test_config.py`, keeping the
  default-config contract; `test_recipe.py` now asserts the recipe installs **no**
  unit). The unit was written for PengyR and is wrong for this image: it ran
  `--config /etc/pengy/settings.json` (this binary takes `--config-dir`), set
  `DynamicUser=yes` and `ProtectHome=yes` — which hide `~/.config/pengy`,
  `~/skills` and `~/.secrets`, all of the agent's state — and wanted
  `multi-user.target` as root. The live recipe
  (`buildroot-external/package/pengy/pengy.mk`) never installed it, so no image
  ever shipped it: a unit describing a service no image ran.
- `PENGY_INSTALL_INIT_SYSTEMD` and the `systemd` dependency from the vendored
  `pengy.mk`, so the vendored recipe matches the live one.

### Changed
- Serving `pengy-web` is a per-user, opt-in act, not a recipe-wide enablement:
  the unit is the system template
  `buildroot-external/board/server/rootfs-overlay/etc/systemd/system/pengy-web@.service`
  (`User=%i`, `WorkingDirectory=%h`, loopback by default, no `DynamicUser`, no
  `ProtectHome`), enabled by [`pengyos-webui`](../pengyos-webui/). README and
  `spec.md` say so; the recipe installs the two binaries and the default config
  and nothing else.

## [0.1.0] - 2026-09-18

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, stdlib-only at test time), the
  `package/pengy/` Buildroot recipe directory, and the pytest harness.
- `.gitignore`, `README.md`, `CHANGELOG.md`, and `LICENSE` (MIT, Copyright
  (c) 2026 Pat Wendorf) so the repo is GitHub-ready from the first commit.
- `tests/conftest.py` (adds the tool root to `sys.path` so a bare
  `python3 -m pytest -q` works without `pip install -e .` — the verifier runs
  exactly that, cwd = `tools/pengy-agent-package`).
- `tests/recipe_check.py` (a pure-stdlib Buildroot `.mk`/kconfig parser, shared
  by the scaffold and recipe checks; unit-tested against a synthetic fixture so
  it has teeth before the real recipe lands).
- `tests/test_scaffold.py` — package metadata, the `package/pengy/` directory,
  and the recipe-check helpers.
- `tests/test_github_ready.py` — the GitHub-ready contract: the four docs exist
  and are non-empty, LICENSE is MIT with the exact copyright line, the README
  documents the recipe and cross-links `spec.md`/`implementation.md`, CHANGELOG
  is Keep-a-Changelog with its newest section equal to the project version, and
  `.gitignore` covers the Python build/test artifacts.
- `package/pengy/pengy.mk` (implementation.md item 2): pins the PengyR agent at
  `PENGY_VERSION = 1.9.0` (`PENGY_LICENSE = MIT`), a verified `https` download
  site, and builds **only** the two headless Rust targets
  (`cargo build --bin pengy-cli --bin pengy-web --locked --offline`), copying
  them into `$(TARGET_DIR)/usr/bin`. The stock `cargo-package` install targets
  the root *library* crate and would install nothing, so `_INSTALL_TARGET_CMDS`
  is overridden. The Qt6 GUI is not a Cargo workspace member, so it is never
  built. `tests/test_recipe.py` (13 tests) asserts the pin, source binding, MIT
  license, verified site, the two-bin build, the exact install set, and the
  absence of any Qt/C++/CMake toolchain.
- `package/pengy/pengy.hash` (implementation.md item 3): the sha256 for the
  pinned PengyR tarball (`PengyR-1.9.0.tar.gz`, matching `PENGY_SOURCE`). SOURCE
  is a *literal* filename (not a `$(PENGY_VERSION)` expansion) so the `.hash`
  filename matches it and `pengyos-recipe-lint`'s `hash_present` rule can verify
  the digest — the recipe lints clean (0 violations). `tests/test_hash.py`
  asserts the sha256 row, that its filename equals the recipe's SOURCE, and that
  the version embedded in the tarball name equals `PENGY_VERSION` (the
  version-consistency contract), plus an opt-in check that the digest matches
  the real downloaded tarball and that the recipe passes `pengyos-recipe-lint`.
  `tests/recipe_check.py` gains `hash_entries` / `hash_sha256_entries`.
- `package/pengy/pengy-web.service` + `package/pengy/settings.json`
  (implementation.md item 4): a hardened systemd unit for the headless web agent
  (`DynamicUser=yes`, `NoNewPrivileges=yes`, `Restart=on-failure`,
  `ProtectSystem`/`ProtectHome`/`PrivateTmp` hardening) and a default
  `/etc/pengy/settings.json` (OpenAI-compatible `base_url`, no API key baked in,
  no default model). The recipe installs both — the unit via
  `PENGY_INSTALL_INIT_SYSTEMD`, the config into `/etc/pengy` — and declares
  `systemd` as its only system dependency. `tests/test_service_and_config.py`
  validates the unit sections/hardening, the config sanity, and the new install
  destinations; `tests/test_recipe.py` gains the config/unit install checks and
  allows `systemd` as the sole non-host dependency.
- `tests/test_recipe_compliance.py` (the "Recipe tests" item): the recipe's
  *compliance* half of the spec contract — it runs the **real** sibling
  `pengyos-recipe-lint` tool (not a reimplementation) against `package/pengy/`
  and asserts zero violations, both via the text `check` report and the parsed
  `--json` report. The individual lint rules (version pinned, hash present, MIT
  license, verified https site) are asserted offline in `test_recipe.py` /
  `test_hash.py`; the headless-only and unit/config assertions live in
  `test_recipe.py` / `test_service_and_config.py`. The opt-in lint run is now
  owned here (moved out of `test_hash.py`) and is opt-in behind `PENGYOS_IT=1`
  because the sibling tool requires Python >= 3.11.
- `tests/test_github_ready.py` (the GitHub-ready contract): asserts the four
  repo-root docs (`README.md`, `LICENSE`, `CHANGELOG.md`, `.gitignore`) exist,
  are non-empty, **and** are git-tracked (a file on disk but untracked is not
  "GitHub-ready"); LICENSE is MIT with the exact `Copyright (c) 2026 Pat
  Wendorf` line and the `AS IS` disclaimer; the README documents `pengy-cli` /
  `pengy-web` / `package/pengy` and cross-links `spec.md` / `implementation.md`;
  CHANGELOG is Keep-a-Changelog with its newest `## [x.y.z] - YYYY-MM-DD` equal
  to the `pyproject.toml` version and in newest-first order; `.gitignore` covers
  the Python build/test artifacts; and the FULL suite is green via an opt-in
  `PENGYOS_IT=1` subprocess re-run (the child clears `PENGYOS_IT` so it can't
  recurse).
