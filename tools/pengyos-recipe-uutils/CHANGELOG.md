# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep-a-Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased
### Added
- `tests/test_github_ready.py`: the GitHub-ready contract (`test_github_ready`)
  asserting `README.md`, the MIT `LICENSE` carrying
  "Copyright (c) 2026 Pat Wendorf", `CHANGELOG.md`, `.gitignore`, and a green
  test suite (the latter verified by re-running the suite in a
  recursion-guarded subprocess).
- `package/uutils-coreutils/Config.in`: `BR2_PACKAGE_UUTILS_COREUTILS` option
  (depends on libc + MMU) with a help block.
- `package/uutils-coreutils/uutils-coreutils.mk`: pinned to coreutils 0.11.0,
  MIT license, and a multicall install that ships the `coreutils` binary plus a
  busybox-style symlink for every utility in `feat_common_core` (78 utils),
  built via Buildroot's `cargo-package` infrastructure.
- Recipe-rule tests (`tests/test_recipe.py`) + authoritative utility fixture,
  asserting the pinned version, MIT license, and the install/symlink plan.
- `package/uutils-coreutils/uutils-coreutils.hash`: pinned sha256 for the
  coreutils 0.11.0 GitHub auto-archive tarball, plus `.hash` rule tests
  (present, 64-hex sha256, filename tracks the pinned SOURCE) and an opt-in
  (`PENGYOS_IT=1`) integration test that verifies the digest against the live
  upstream tarball.

## [0.1.0] — 2026-09-15
### Added
- Repository scaffold: Buildroot recipe tree under `package/uutils-coreutils/`,
  `pyproject.toml`, and a pytest harness.
- GitHub-ready metadata: `README.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat
  Wendorf), `CHANGELOG.md`, and `.gitignore`.
