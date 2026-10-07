# Changelog

All notable changes to **pengyos-initramfs** are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Explicit `directories = [...]` include list: paths listed there are packed
  as cpio **directory entries** (name with a trailing `/`, empty data, mode
  `0o040755`, normalized uid/gid/mtime). The kernel's initramfs populate does
  not create missing parent directories, so this is what makes nested file
  paths (e.g. `etc/os-release`) extractable at boot. A `directories` path
  that is missing under `--root` or not a directory is a build error (exit
  code 5); a symlink to a directory is accepted and packed as a plain
  directory entry. Cross-kind duplicates are packed once, with precedence
  `directories` > `binaries` > `files` > `modules`. Covered by new spec,
  builder, and CLI tests plus a `PENGYOS_IT=1` check that real GNU cpio
  lists the directory entries with a trailing slash. Suite is now 126
  passing (123 + 3 opt-in integration).
- GitHub-ready contract test `test_github_ready` (`tests/test_repo.py`): the
  single unified assertion a reviewer greps for — README.md (authored,
  documents the CLI interface, links the license), LICENSE (MIT,
  "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md, `.gitignore`, AND a full
  green test suite (run in a fresh `pytest` subprocess, excluding the
  self-referential test to avoid recursion). The per-file checks in
  `tests/test_scaffold.py` remain the focused unit tests. Suite is now 104
  passing (102 + 2 opt-in integration).
- Reproducibility self-check (`tests/test_repro.py`): a dedicated test that
  builds the same spec + root **twice and asserts an identical sha256** —
  in-process (`build_archive`), across two fresh `parse_spec` calls, and
  end-to-end via **two independent `pengyos-initramfs build` subprocesses**
  (the strongest no-root, no-network proof; any wall-clock, ordering, random
  inode, or gzip FNAME/MTIME nondeterminism would show up as a digest
  mismatch). Suite is now 103 passing (101 + 2 opt-in integration).
- Minimal `/init` generator (`pengyos_initramfs/initgen.py`): every archive
  now contains a generated POSIX `sh` init script at `[init].path` (default
  `init`), mode `0o100755` by default (configurable via `[init].mode`). The
  script (`#!/bin/sh`, `set -eu`, mounts `proc`/`sysfs`/`devtmpfs` after
  `mkdir -p`, then `exec /bin/sh`) is deterministic — a pure function of the
  spec, verified syntactically valid with `sh -n`. An operator path
  explicitly listed in an include list wins over the generated script.
- `tests/test_initgen.py` covering the generated script (shebang, `sh -n`
  syntax check, determinism), the packed entry (path/mode/metadata, sorted
  position, custom path/mode, include-list precedence), and the round-trip
  through real GNU cpio under `PENGYOS_IT=1`.
- Deterministic `newc` cpio writer (`pengyos_initramfs/cpio.py`): hand-rolled,
  standard-library-only implementation of the `070701` format exactly as the
  Linux kernel's initramfs unpacker expects (110-byte headers, kernel
  `N_ALIGN` name padding, zero padding, 1-based inodes, `TRAILER!!!`).
  Verified against real GNU cpio by the opt-in `PENGYOS_IT=1` tests.
- Deterministic builder (`pengyos_initramfs/builder.py`): walks
  `Spec.all_paths()` (sorted), packs exactly the spec'd paths with normalized
  uid/gid/mtime, per-kind normalized modes (binaries `0o755`, files/modules
  `0o644`, symlinks `0o120777`), symlink support, and a zeroed-header gzip
  container — two builds of the same inputs are byte-identical.
- `build` now writes the `.cpio.gz` archive end to end (exit code 0).
- New exit code 5 (`EXIT_BUILD_FAILED`) for build errors (spec path missing
  under the root, directory where a file is expected, unwritable `--out`).
- Test suite grew: `tests/test_cpio_writer.py` (byte-level format checks via
  an independent `tests/cpio_reader.py`), `tests/test_builder.py`,
  `tests/test_cli_build.py`, and opt-in `tests/test_integration.py`
  (real `cpio -t` / `cpio -idm` round-trip, skipped without `PENGYOS_IT=1`).
- Spec parsing (`pengyos_initramfs/spec.py`): parses the TOML `initramfs.toml`
  into a validated, normalized `Spec` (which binaries/files/modules to include,
  plus `[build]` determinism settings and `[init]` generator settings).
  Rejects absolute paths, `..` traversal, and malformed TOML/mode/mtime values
  with a `SpecError`.
- `build` parses/validates the spec; invalid specs exit with
  `EXIT_INVALID_SPEC` (4) and a clear error.
- `examples/initramfs.toml` sample spec (validated by the test suite).

### Changed
- Archive content: every build now includes the generated init entry in its
  sorted position (so inodes of later entries shift by one). The CLI
  success message reports the true entry count.
- `requires-python` raised to `>=3.11` (uses stdlib `tomllib`).
- Exit code 3 (`EXIT_NOT_IMPLEMENTED`) removed: `build` is implemented.
- `spec.md` extended with the discovered/recorded archive-format details
  (header layout, `N_ALIGN` padding, mode normalization, gzip container,
  exit codes) — see AGENTS.md.

## [0.1.0] - 2026-09-16

### Added
- Repository scaffold: `pengyos_initramfs` package with a `pengyos-initramfs`
  console entry point.
- CLI argument surface (`build --root --spec --out`, `--version`, `--help`);
  `build` validates its inputs and exits with a distinct
  "not yet implemented" code at this stage.
- `pyproject.toml` (setuptools, no runtime dependencies) and pytest config.
- Docs (`README.md`, `LICENSE`, `.gitignore`) and smoke tests.
