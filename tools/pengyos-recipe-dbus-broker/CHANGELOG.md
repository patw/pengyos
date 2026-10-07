# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-09-15

### Added
- `tests/test_github_ready.py::test_github_ready_files_are_tracked_by_git`: the
  GitHub-ready contract now asserts that `README.md`, `LICENSE`, `CHANGELOG.md`
  and `.gitignore` are not merely present on disk but **tracked by git** — a file
  that exists on disk but is untracked is not "GitHub-ready" (it would surface in
  `git status` as untracked and would not be pushed). Backed by a
  `_git_tracked()` helper that shells out to `git ls-files --error-unmatch`
  (falls back to "not a git checkout" when the suite runs from an unpacked
  tarball). This closes the last gap in the GitHub-ready contract
  (implementation.md item 5).

### Changed
- README Status table marks the `Tests` and `test_github_ready` rows done
  (docs kept in sync with the green suite, per the GitHub-ready contract).

## [0.1.3] - 2026-09-15

### Added
- `tests/recipe_check.py::installed_init_files`: the general form of
  `installed_systemd_units` — it returns *every* `$(TARGET_DIR)/<dest>` install
  destination named in the `.mk`, so it catches both the systemd units and the
  D-Bus config files that the recipe ships. Unit-tested in
  `tests/test_scaffold.py`.
- `tests/test_recipe.py::test_systemd_socket_install_destination` and
  `test_dbus_config_files_installed`: assert the `dbus-broker.mk` installs
  `dbus.socket` to `$(TARGET_DIR)/usr/lib/systemd/system/` and the
  `session.conf` / `system.conf` config files to `$(TARGET_DIR)/usr/share/dbus-1/`
  — closing the last gap in the "unit installed" half of the spec Verification
  contract (implementation.md item 4).

## [0.1.2] - 2026-09-15

### Added
- `tests/test_recipe.py`: checks that the Buildroot recipe under
  `package/dbus-broker/` is authored to the spec contract — `DBUS_BROKER_VERSION`
  is pinned to `37` and the source tarball is bound to it; `DBUS_BROKER_LICENSE`
  is Apache-2.0 (with the upstream `and/or` LGPL-2.1+ subproject clause);
  `DBUS_BROKER_DEPENDENCIES` includes `systemd` + `expat` and the `dbus`
  dependency is conditional on `BR2_PACKAGE_DBUS`; the `Config.in` kconfig entry
  `depends on BR2_PACKAGE_SYSTEMD` and `select BR2_PACKAGE_EXPAT`; the `.mk`
  installs the `dbus.socket` systemd unit into
  `$(TARGET_DIR)/usr/lib/systemd/system/` and the unit file ships in
  `package/dbus-broker/`; and the recipe ends with
  `$(eval $(meson-package))`.

### Changed
- `package/dbus-broker/Config.in`: added the `depends on BR2_TOOLCHAIN_HAS_SYNC_4`
  kconfig guard (needed because the recipe `select`s `BR2_PACKAGE_LIBCAP_NG` when
  `BR2_PACKAGE_AUDIT` is on, and libcap-ng requires `__sync` builtins). The
  `Config.in` now matches the upstream `buildroot/buildroot`
  `package/dbus-broker/Config.in` exactly.

## [0.1.1] - 2026-09-15

### Added
- `package/dbus-broker/dbus-broker.hash` (implementation.md item 3): the
  Buildroot hash file for the pinned `dbus-broker` 37 source — the tarball
  digest plus the `LICENSE` digest and the eight subproject `AUTHORS`/`README`
  digests named in `DBUS_BROKER_LICENSE_FILES`. Every digest was verified
  against the real upstream release tarball.
- `tests/test_hash.py`: checks that the `.hash` exists, uses the Buildroot
  `sha256  <digest>  <path>` line format with 64-hex digests, names the
  tarball bound to the version pinned in `dbus-broker.mk`, records the
  `LICENSE` + all subproject files, and that the recorded digests equal the
  real sha256 of a committed fixture tarball (offline, no network).
- `tests/fixtures/dbus-broker-37.tar.xz`: the pinned upstream release used as
  the offline fixture for the hash-correctness tests.

### Fixed
- `tests/test_scaffold.py::test_package_dir_is_tracked_by_git`: the directory
  is now tracked via the real recipe files (item 2) instead of the `.gitkeep`
  placeholder that item 2 removed, so the suite stays green.

## [0.1.0] - 2026-09-15

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, stdlib-only at test time), the
  `package/dbus-broker/` Buildroot recipe directory, and the pytest harness.
- `.gitignore`, `README.md`, `CHANGELOG.md`, and `LICENSE` (MIT, Copyright
  (c) 2026 Pat Wendorf) so the repo is GitHub-ready from the first commit.
- `tests/conftest.py` (adds the tool root to `sys.path` so a bare
  `python3 -m pytest -q` works without `pip install`),
  `tests/test_scaffold.py` (package metadata, the `package/dbus-broker/`
  directory, the recipe-check helpers), and `tests/test_github_ready.py`
  (the GitHub-ready contract: the four docs exist and are non-empty, LICENSE is
  MIT with the exact copyright line, the README documents the recipe and
  cross-links `spec.md`/`implementation.md`, CHANGELOG is Keep-a-Changelog with
  its newest section equal to the project version, and `.gitignore` covers the
  Python build/test artifacts).
