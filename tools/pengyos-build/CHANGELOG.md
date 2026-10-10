# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- `build` passed `LD_LIBRARY_PATH` to make unchanged, so a host with an empty
  entry in it (`/usr/local/cuda/lib64:`, a trailing colon) failed Buildroot's
  "current working directory in your LD_LIBRARY_PATH" check before building
  anything. Only `configure`'s `make show-info` call dropped the variable. The
  build environment now goes through `hostshim.clean_ld_library_path`, which
  drops empty and `.` entries, keeps real directories, and unsets the variable
  when none are left.

### Removed
- The tool-local `targets.toml` sample (it still named the retired M1
  target and shadowed the repo-root file when run from this directory).

### Added
- `configure` also rebuilds a built package whose **resolved dependencies**
  changed although its own options did not (`make show-info` before and after;
  `stale.dependency_changes`). Packages turn on optional support for whatever
  else is in the build — mesa3d when libglvnd appeared, cups and sane-backends
  when avahi did — and those were left built without it.
- **`configure` rebuilds packages whose options changed** (`pengyos_build/stale.py`).
  Buildroot skips any package with a `.stamp_built`, so re-configuring an
  existing output dir with a changed defconfig silently kept the old build: the
  rtw88 Wi-Fi firmware was enabled in the Desktop and Core-M2 defconfigs but
  absent from their images, because linux-firmware had been built before the
  option arrived. `configure` now diffs the previous `.config` against the new
  one, maps every changed `BR2_PACKAGE_*` symbol to the longest built package
  that owns it (`BR2_PACKAGE_LINUX_FIRMWARE_RTL_RTW88` -> `linux-firmware`), and
  removes that package's build dir (what `make <pkg>-dirclean` does) so the next
  `build` rebuilds it. Packages the new config disables are reported (their files
  stay in `target/` until a clean build). Not covered: rebuilding a package
  because a *different* package it can use was enabled (e.g. mesa3d after
  libglvnd).
- **`sbom` subcommand** (`pengyos_build/sbom.py`): drives Buildroot's
  `legal-info`/`show-info` (via the GNU host shim) through `pengyos-sbom` into
  an SPDX 2.3 SBOM (+ CycloneDX) at `<out>/images/<target>.spdx.json`;
  `--sign-key` adds a detached ed25519 signature and `--embed` re-runs the
  build so the board post-build bakes the SBOM into the rootfs (`/etc/pengyos/`).
  Wires provenance (US-CORE-6) into the build.
- `tests/test_repo.py::test_github_ready`: the unified GitHub-ready contract
  assertion (spec.md Verification). It verifies README.md (authored, links
  the license), LICENSE (MIT + the exact "Copyright (c) 2026 Pat Wendorf"
  line), CHANGELOG.md (authored with the 0.1.0 section), and .gitignore, then
  runs the full test suite in a fresh subprocess and asserts it is green. The
  sub-run deselects `test_github_ready` itself (via `-k`) to avoid infinite
  recursion; a non-zero return code (any failure, skip, or collection error)
  fails the check with a tail of the pytest output for debuggability. The
  pre-existing per-file repo checks remain as focused unit tests.
- `pengyos-build acquire <target> [--targets PATH] [--build-root DIR]
  [--remote URL]` CLI subcommand: pins the target's Buildroot source tree
  (blobless clone + checkout of the release tag) before `configure`/`build`,
  reusing `pengyos_build.acquire.acquire_target_tree`. Prints the one-line
  acquisition summary (`<action>: <path> @ <tag> (<commit12>)`) on stdout and
  exits 0; a targets-file error, an unknown target, or an `AcquireError`
  (git failure, or a destination that exists but is not a git work tree)
  prints `pengyos-build: error: …` on stderr and exits 1. `--remote` overrides
  the upstream remote (default `https://git.buildroot.net/buildroot.git`).
  CLI process tests drive a fake `git` on PATH (blobless clone + tag checkout
  asserted, idempotent `up-to-date` re-entry runs only read-only probes,
  `--remote` override honoured, git-failure/non-git-dest/unknown-target/bad-file
  all exit 1).
- `--dry-run` for the make-invoking subcommands (`configure`, `build`,
  `clean`): resolves the exact make invocation (same path resolution and argv
  ordering; for `build` the resolved `-j<jobs>`) and prints it on stdout as
  one shell-quoted line (each element `shlex.quote`d), then exits 0 without
  running make, creating the output dir, writing any log, or applying the
  host shim. The same precondition checks apply as a real run, so a missing
  tree / unconfigured target is still an exit-1 error. Hermetic unit tests
  (fake spawner never called, nothing written) plus CLI process tests with a
  sentinel-recording fake `make`.
- `pengyos_build.manifest`: the build manifest — a reproducible receipt of a
  target's build, written as **deterministic JSON** (sorted keys, fixed
  indent → byte-identical for identical artifacts). It is a pure read of the
  external tree and the per-target output dir (no make, no network; it writes
  only the manifest file). Records the **inputs** (pinned `br_version`,
  defconfig name + its **SHA-256** from `$BR2_EXTERNAL/configs/`, board when
  set, external-tree path + its `git describe --always --dirty`), the
  **SHA-256 of every output image** under `<out>/images` (keyed by relative
  path, sorted), a **start/end timing window** derived from the artifacts'
  mtimes (`null` when unbuilt) plus a wall-clock `generated_at`, and
  `schema_version` + the tool version. A missing external tree or
  `configs/<defconfig>` is a `ManifestError` (CLI exits 1); a non-git
  external tree is fine (its `git describe` is `null`). Injectable git runner
  and clock; hermetic unit tests including "hashes match written files" and
  "deterministic field ordering" checks.
- `pengyos-build manifest <target> [--json OUT] [--targets PATH]
  [--build-root DIR]` CLI subcommand (default OUT is
  `<output>/build-manifest.json`; prints the path written; exit 1 on
  targets-file or manifest errors).
- `pengyos_build.clean`: the clean step — runs Buildroot's own clean
  target, `make -C <src> O=<out> BR2_EXTERNAL=<ext> [BR2_BOARD=<board>] clean`,
  scoped to the target's per-target `O=` output dir: it removes built
  artifacts while **keeping `.config`** (the target stays configured), and
  never touches the acquired source tree or the synced repo. make's
  stdout+stderr are captured **merged, in order** into
  `<out>/logs/clean.log` (truncated, never appended); the GNU-over-uutils
  host shim applies exactly as in configure. Missing source tree →
  `CleanError` suggesting `acquire`; missing external tree → `CleanError`;
  an unconfigured target (no output dir) → `CleanError` suggesting
  `configure`; a non-zero make exit names the exit code and the log.
  Injectable `subprocess.run`-like runner; hermetic unit tests including a
  scoping test proving nothing outside the output dir changes.
- `pengyos-build clean <target> [--targets PATH] [--build-root DIR]` CLI
  subcommand (exit 1 with a clear message on targets-file, clean, or make
  errors).
- `pengyos_build.build`: the build step — runs
  `make -C <src> O=<out> BR2_EXTERNAL=<ext> [BR2_BOARD=<board>] -j<N>` against
  an already-configured target. `-j<N>` defaults to `-j$(nproc)`
  (`os.cpu_count()`, clamped to ≥1) and is configurable; the defconfig is NOT
  re-passed (buildroot builds the configured `.config` without re-running
  kconfig), while `BR2_EXTERNAL`/`BR2_BOARD` are re-passed for symmetry. make's
  stdout+stderr are **streamed, merged, in order** into
  `<out>/logs/build.log` (truncated, never appended), so the log grows as the
  build runs. Missing source tree → `BuildError` suggesting `acquire`; an
  unconfigured target (no output dir) → `BuildError` suggesting `configure`; a
  non-zero make exit names the exit code and the log. Injectable
  `subprocess.run`-like runner; hermetic unit tests.
- `pengyos_build.build.detect_stage` / `status_for`: report the build stage by
  scanning `<out>/logs/build.log` (a pure file read — no make, no network).
  Stages in build order: `fetching` → `building` → `images` → `done`; a
  missing/empty log is `not_started`; a make-failure marker
  (`*** [ … ] Error` / a compiler `error:` line) is `error` and wins over the
  furthest stage. The reported stage is the furthest marker present.
- `pengyos-build build <target> [--jobs N] [--targets PATH]
  [--build-root DIR]` and `pengyos-build status <target> [--targets PATH]
  [--build-root DIR]` CLI subcommands (build exits 1 on targets-file/build/make
  errors; status prints one line `<target>: <stage>` and exits 0).
- `pengyos_build.configure`: the configure step — runs
  `make -C <src> O=<out> BR2_EXTERNAL=<ext> [BR2_BOARD=<board>] <defconfig>`
  against an already-acquired tree, capturing make's stdout+stderr **merged,
  in order** into `<out>/logs/configure.log` (truncated, never appended). Per
  target output dir `<build-root>/output/<target>`; the GNU-over-uutils host
  shim lives in `<out>/.hostbin`, so every write stays inside the output
  dir. Missing source/external trees are hard errors suggesting
  `pengyos-build acquire <target>`; a non-zero make exit names the exit code
  and the log. Injectable `subprocess.run`-like runner; hermetic unit tests.
- `pengyos-build configure <target> [--targets PATH] [--build-root DIR]`
  CLI subcommand (exit 1 with a clear message on targets-file, configure,
  or make errors).
- `pengyos_build.hostshim`: host-tooling hygiene — prefer GNU coreutils over
  uutils via a no-root `PATH` shim (SPEC §8 gotcha: uutils `install` trips
  Buildroot's host-tool dependency check). Discovers `gnu*` binaries,
  resolves each tool's default location, plans a shim for every tool whose
  default resolves into a `coreutils` dir, and materialises idempotent
  `shim_dir/<tool> -> gnu<tool>` symlinks (only tools that are actually uutils
  are shimmed; a stock-GNU host produces none). Resolver and runner are
  injectable for hermetic tests.
- `pengyos_build.acquire`: Buildroot acquisition — blobless clone
  (`git clone --filter=blob:none --no-checkout`) + checkout of the pinned
  release tag, with idempotent re-entry (`up-to-date` / `checked-out` /
  `fetched-checked-out`), a post-checkout HEAD post-condition, and a
  deterministic per-version tree location
  (`$PENGYOS_BUILD_ROOT` or `~/build/pengyos` → `buildroot-<version>`).
  Injectable git runner; real-git integration test behind `PENGYOS_IT=1`.
- `targets.toml` schema: `pengyos_build.targets` parses/validates the
  declarative targets file (name, br_version, external_tree, defconfig,
  optional board) with precise, file/entry-scoped error messages.
- `pengyos-build list [--targets PATH]` prints parsed targets (exit 1 with a
  clear message on validation errors).
- Committed example `targets.toml` with the `pengyos-core-m1` target pinned
  to Buildroot 2026.02.3 LTS.

## [0.1.0] - 2026-09-13

### Added
- Initial repo scaffold: `pyproject.toml` (setuptools, stdlib-only runtime,
  `pengyos-build` console script), `pengyos_build/` package with an argparse
  CLI (`--help`, `--version`, `version` subcommand) and `__main__`.
- pytest harness (no network, no root; integration tests opt-in via
  `PENGYOS_IT=1`).
- README, MIT LICENSE, CHANGELOG, .gitignore.
