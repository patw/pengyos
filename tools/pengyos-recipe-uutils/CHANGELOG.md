# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep-a-Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.1 — 2026-10-25

### Fixed
- The authored recipe is pinned to uutils/coreutils **0.12.0**, matching the build
  tree. The nightly routine's bump (2026-10-20, image 26.10.36) edited
  `buildroot-external/package/uutils-coreutils/` only, so this copy drifted and
  `test_tree_copy_is_identical_to_the_authored_copy` went red on `main`, taking
  `test_github_ready` with it. The `.hash` comment now records the raw upstream
  archive digest for 0.12.0 (`4fb32765…e122`, verified against the live tarball on
  2026-10-25) beside Buildroot's post-processed one.
- The authoritative fixture is re-pinned: `coreutils-0.12.0-unix-utilities.txt`.
  The set is unchanged at 106 names — 0.12.0 folded upstream's
  `feat_require_unix_hostid` into `feat_common_core` (hostid is still shipped) —
  and its header now records that, plus that `stdbuf` comes from
  `feat_external_libstdbuf`, which PengyOS enables deliberately.
- `test_hash_matches_downloaded_source` (opt-in, `PENGYOS_IT=1`) could never pass:
  it compared the `.hash` **body** — Buildroot's post-processed tarball — with the
  raw GitHub archive. It now checks the pair the comment records (tag + raw
  archive digest) plus the body's filename, which is what actually drifts.
  Mutation-checked: corrupting the recorded digest fails it.

## Unreleased
### Added
- `test_hash_line_format_is_buildroots`: the `.hash` body uses Buildroot's
  spelling `sha256  <hash>  <filename>`. A bare `sha256sum`-style line is never
  matched by `support/download/check-hash` (it reads `read -r t h f`), and the
  download fails with "No hash found for coreutils-0.11.0.tar.gz".
- The pinned digest is of Buildroot's **post-processed** tarball
  (`ec311bfd…6302`) — the cargo post-process re-packs the source with the
  vendored crates and re-verifies, same as greetd/oo7/starship. The raw upstream
  0.11.0 archive digest (`a4796611…f70a4`, verified against the live tarball) is
  recorded in the `.hash` comment. `make uutils-coreutils-source` is green.

### Added
- `test_build_selects_the_unix_feature_set`: the build must use
  `--no-default-features --features unix` (upstream's `feat_os_unix`), offline and
  locked. The default feature set has no stat, id, sync, uname, whoami, install,
  stty, timeout, hostname, groups, nice, nohup, chroot, chgrp, chmod, chown,
  nproc, mkfifo, mknod, logname, hostid, users, who, uptime, pinky, stdbuf, kill,
  arch - every one of which PengyOS Core ships today.
- `tests/fixtures/coreutils-0.11.0-unix-utilities.txt`: the authoritative 106-name
  `feat_os_unix` set for 0.11.0, generated from the pinned Cargo.toml's feature
  sets (replaces the 78-name `feat_common_core` fixture).
- `test_tree_copy_is_identical_to_the_authored_copy`: the recipe the build uses
  (`buildroot-external/package/uutils-coreutils`) must be byte-identical to the
  one authored here.
- `Config.in` gates on `BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS`, MMU and
  glibc (the utmpx utilities - who/users/uptime/pinky - are stubs on musl) and
  selects `BR2_PACKAGE_HOST_RUSTC`.
- The pinned sha256 was verified against the live 0.11.0 tarball on 2026-10-12.

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
