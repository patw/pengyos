# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-19

### Added
- **Re-added `fzf` + `starship` to the comfort set (implementation.md follow-up).**
  Now that this repo ships well-formed Buildroot packages for both
  (`package/fzf/` — a `golang-package`, `package/starship/` — a `cargo-package`;
  implementation.md items 16 + 19), their symbols (`BR2_PACKAGE_FZF` /
  `BR2_PACKAGE_STARSHIP`) are defined in the tree and they are re-enabled by the
  Buildroot *fragment*. `toolset.TOOL_TABLE` gains `fzf` (after `eza`) and
  `starship` (after `zoxide`), both with `exists_in_buildroot=True`; the fragment
  was re-emitted (`BR2_PACKAGE_FZF=y` / `BR2_PACKAGE_STARSHIP=y` restored).
  `shell/fzf.sh` (restored, with a multi-root probe for the packaged
  key-bindings/completion scripts instead of a hardcoded Debian path) and
  `shell/starship.toml` (restored, `telemetry = false`) were brought back, and
  `shell/profile` now sources `fzf.sh` and loads `starship` via a
  `PENGYOS_TOOLSET_STARSHIP_LOADED`-guarded `starship init`. The tests were
  updated to the 10-tool set: `test_toolset.py` (the dropped-tools test became a
  positive "fzf/starship re-added with canonical symbols" test; the count
  assertion 8→10; `test_all_shipped_tools_exist_in_buildroot` now covers all 10),
  `test_shell.py` (fzf.sh added to artifacts/idempotency/network checks + new
  runtime tests that `fzf.sh` sets `FZF_DEFAULT_COMMAND`/`FZF_DEFAULT_OPTS` and is
  idempotent; the profile now sources fzf and wires starship),
  `test_syntax.py` (fzf.sh back in the `sh -n` snippet set), and
  `test_no_telemetry.py` (fzf.sh + starship.toml added to the scanned config set
  plus a positive `telemetry = false` assertion for starship.toml). The suite is
  green (126 passed, 1 skipped) with no Buildroot/make/Rust/Go/network at test
  time. `spec.md`, `README.md` and `AGENTS.md` updated.
- **A Buildroot cargo-package for `starship` (implementation.md follow-up).**
  `package/starship/{Config.in,starship.mk,starship.hash}` — and a
  structural/defconfig test proving it is a *well-formed* Buildroot **cargo**
  (Rust) package. **starship is a Rust program** (its `Cargo.toml` carries
  `edition = "2024"`, `Cargo.lock`, and `build.rs`), so it is a Buildroot
  `cargo-package`, **not** a `golang-package` like fzf: `Config.in` defines a
  `bool` `config BR2_PACKAGE_STARSHIP` that depends on
  `BR2_PACKAGE_HOST_RUSTC_TARGET_ARCH_SUPPORTS` and selects `BR2_PACKAGE_HOST_RUSTC`,
  `starship.mk` pins `STARSHIP_VERSION = 1.26.0`, points `SITE` at
  `github.com/starship/starship`, records `STARSHIP_LICENSE = ISC`, and evaluates
  `$(eval $(cargo-package))`. The digest is of the *raw* github archive
  `starship-1.26.0.tar.gz` (unlike a Go package, whose digest is of the vendored
  `go2` repack) — computed by downloading the archive that `$(call github, ...)`
  builds and hashing it (`sha256 8c95e8a6…a59`, `sha512 88612fba…cc280`).
  `tests/test_package_buildroot.py` gained a `validate_cargo_package()` plus 9
  starship tests (well-formedness + teeth: a missing host-Rust dependency, wrong
  symbol, missing `cargo-package` eval, corrupted digest, mis-named tarball, and
  a dropped license are each asserted to be flagged). The suite now has 17
  buildroot tests (8 fzf + 9 starship) and stays green with no Buildroot/`make`/
  Rust toolchain/network at test time. The package is authored but **not** yet
  wired into `toolset.TOOL_TABLE` (the follow-up re-adds fzf and starship
  together, once both packages exist); `tests/test_toolset.py` still asserts the
  8-tool set and that `fzf`/`starship` stay dropped.
- **A Buildroot Go-package validator + the first shipped package, `fzf`
  (implementation.md follow-up).** `tests/test_package_buildroot.py` (8 tests)
  asserts a `package/<pkg>/` is a *well-formed* Buildroot Go package without ever
  invoking Buildroot, `make`, a Go toolchain or the network: it parses
  `Config.in` (a `bool` `config BR2_PACKAGE_<X>` that depends on
  `BR2_PACKAGE_HOST_GO_TARGET_ARCH_SUPPORTS` and selects `BR2_PACKAGE_HOST_GO`),
  `<pkg>.mk` (pinned version, github `SITE`, `GOMOD`, `LICENSE`, a
  `$(eval $(golang-package))`), and `<pkg>.hash` (names the *vendored*
  `<name>-<version>-go2.tar.gz`, not the raw upstream tarball, with the expected
  sha256/sha512). The teeth are real: a missing host-Go dependency, a wrong
  symbol, a missing `golang-package` eval, a corrupted digest, and a hash that
  names the raw tarball are each asserted to be flagged. The committed `fzf`
  package (`package/fzf/{Config.in,fzf.mk,fzf.hash}`, version `0.74.4`) is the
  first concrete step of the follow-up; its digest was computed with Buildroot's
  *own* `support/download/go-post-process` + `helpers` (`go mod vendor` then a
  reproducible repack — fixed mtime from the first extracted file, sorted,
  `--owner=0 --group=0`), so it is byte-for-byte what a real `make fzf-download`
  produces and re-verified to be reproducible. `fzf`'s main package lives at the
  module root (`module github.com/junegunn/fzf`, `package main` in `main.go`), so
  the default `golang-package` build target `.` yields the `fzf` binary; the
  download URL Buildroot constructs (`.../archive/v0.74.4/fzf-0.74.4-go2.tar.gz`)
  was confirmed to resolve (HTTP 200) to the raw source. The package is authored
  but **not** yet wired into `toolset.TOOL_TABLE` (the follow-up re-adds fzf and
  starship together, once both packages exist); `tests/test_toolset.py` still
  asserts the 8-tool set and that `fzf`/`starship` stay dropped.
- **Dropped `fzf` / `starship` from the comfort set (implementation.md item 7).**
  Verified against the 2026-09-18 Buildroot `master` checkout (raw-file check:
  `package/{tmux,mosh,ripgrep,zoxide,bat,eza,fd,jq}/Config.in` → HTTP 200;
  `package/{fzf,starship}/Config.in` → HTTP 404) that `fzf` and `starship` have
  **no** Buildroot package — `BR2_PACKAGE_FZF` / `BR2_PACKAGE_STARSHIP` do not
  exist. Since the deliverable is a Buildroot *fragment* (which can only enable
  packages that exist), the two were dropped from `toolset.TOOL_TABLE`; the
  fragment was re-emitted (dropped `BR2_PACKAGE_FZF=y` / `BR2_PACKAGE_STARSHIP=y`);
  `shell/fzf.sh` and `shell/starship.toml` were deleted; the fzf-source +
  starship-prompt blocks were removed from `shell/profile`; and `spec.md`'s tool
  list / Interface were revised. The comfort set is now the 8 tools that actually
  ship: mosh, tmux, ripgrep, fd, bat, eza, zoxide, jq. `tests/test_toolset.py`
  now asserts every shipped tool maps to a Buildroot package that exists
  (`test_all_shipped_tools_exist_in_buildroot`) and that the dropped tools stay
  absent (`test_dropped_tools_still_absent_from_table`); `test_shell.py`,
  `test_syntax.py` and `test_no_telemetry.py` were trimmed of fzf/starship
  (the no-telemetry scan keeps its **teeth** — planted `https://`/SDK/
  `telemetry=true`/shell `curl` are still flagged). `spec.md`, `README.md` and
  `AGENTS.md` updated. Adding real Buildroot packages for fzf/starship is a
  separate, large deliverable ("building the image" — an explicit non-goal) and
  is tracked as a follow-up.
- **The no-telemetry assertion over the config set** (implementation.md item 6):
  `tests/test_no_telemetry.py` (16 tests) asserts that the whole comfort config
  set (`tmux.conf`, `starship.toml`, `profile`, `fzf.sh`, `zoxide.sh`) never
  phone homes — no analytics/telemetry endpoint, no analytics SDK, no explicit
  telemetry *enablement*. The shared scanner (`conftest.scan_config_for_telemetry`
  / `strip_config_comments` / `NO_TELEMETRY_TOKENS`) strips comments first, so a
  comment that merely *mentions* "telemetry" does not trip it (that is also
  starship's config key, set to `false`); telemetry is checked **positively**
  (`telemetry = false` present, `telemetry = true` absent) rather than by the
  bare word. The scan has **teeth**: a planted `https://` phone-home URL, a
  planted analytics SDK, a planted `telemetry = true`, and a planted shell `curl
  https://…` are each asserted to be flagged, while the `telemetry = false`
  opt-out and mere mentions in comments are not (mutation-verified). This closes
  the spec's "no-telemetry scan flags a planted phone-home URL" requirement. The
  now-duplicated network/telemetry scans were consolidated out of
  `tests/test_shell.py` into this module (the shell-command network tokens
  `curl`/`wget`/`npm`/`pip`/`git` stay in `test_shell.py`, which owns item 3's
  "no network at shell start").
- **The syntax checks** (implementation.md item 5): `tests/test_syntax.py`
  (18 tests) gates the comfort configs on two structural checks — every POSIX-sh
  snippet (`profile`, `fzf.sh`, `zoxide.sh`) passes `sh -n` (dash, parse-only,
  no runtime/network/root), and `tmux.conf` parses via a small structural parser
  (`conftest.parse_tmux_conf` / `validate_tmux_conf`) that tolerates comments,
  strips leading option flags, keeps quoted `#[fg=...]` values intact, and
  raises on the first invalid directive (unknown verb, missing option/value,
  malformed option/key). The tests have teeth: a broken `if` fails `sh -n`, and
  each tmux failure mode (unknown verb, missing value/bound-command, bad option,
  bad key) is asserted to be reported. Also fixes a real bug found by the gate:
  `shell/tmux.conf` had `setw -g monitor-off`, which is invalid tmux (a
  `setw` needs an option *and* a value) — replaced with the valid directive that
  captures the intent, `set -g monitor-interval 0` (monitoring interval 0 =
  off).
- **The drift-proof validator** (implementation.md item 4): `toolset.validate()`
  is the single entry point that returns a list of error strings (empty ==
  drift-free) and asserts, with teeth, that (1) every promised tool maps to a
  well-formed `BR2_PACKAGE_*` symbol (no stub/placeholder tokens), (2) no
  duplicate tool names, (3) no duplicate symbols (the mapping is bijective),
  and (4) the emitted fragment round-trips back to the exact same ordered
  symbols. `toolset.parse_fragment()` is the inverse of the emitter, and
  `toolset.assert_valid()` / the `python3 -m toolset validate [PATH]` CLI turn
  that into a one-shot gate (exit non-zero on drift). `tests/test_validator.py`
  (17 tests) exercises every failure mode with deliberately broken tables and a
  corrupted fragment, confirms the real table + committed fragment validate
  clean, and checks the CLI exits 0/1 correctly. This closes the gap between
  item 2 (emit) and a real drift gate: the tool set and the fragment can no
  longer drift apart.
- **The shell comfort profile + configs** (implementation.md item 3): `shell/`
  now ships the authored, committed artifacts — `profile` (bash + zsh login
  profile: deduped local-PATH prep, comfort aliases, Starship prompt, and it
  sources `fzf.sh`/`zoxide.sh`), `tmux.conf` (truecolor, mouse, sane prefix,
  reload-on-key, status line), `starship.toml` (compact theme, `telemetry =
  false`), and the offline `fzf.sh` / `zoxide.sh` init snippets (each guarded by
  a `PENGYOS_TOOLSET_*` idempotency marker; zoxide falls back to a no-op `z`
  shim when the binary is absent). Every snippet is POSIX-sh (dash `sh -n`) and
  makes NO network call at shell start — only `command -v` checks + local
  sourcing — so a fresh offline login works. `tests/test_shell.py` (16 tests)
  asserts the artifacts exist and are tracked, each has an idempotency guard, no
  network/telemetry-enabled tokens appear, `starship.toml` parses and disables
  telemetry, `tmux.conf` carries the promised directives, and the profile is
  idempotent at runtime (double-sourcing does not duplicate PATH or re-run the
  body). The no-token scan strips comments first so it checks code, not
  documentation, and mutation checks confirm the tests have teeth.
- **The promised comfort tool set as data + a deterministic Buildroot fragment**
  (implementation.md item 2). `toolset.py` is the single source of truth: an
  ordered `TOOL_TABLE` mapping each promised tool (mosh, tmux, ripgrep, fd, bat,
  eza, fzf, zoxide, starship, jq) to exactly one `BR2_PACKAGE_*` symbol, plus
  `emit_fragment()` / `write_fragment()` and a `python3 -m toolset emit` CLI.
  `fragments/server-toolset.fragment` is generated from the table, so the tool
  set and the fragment cannot drift apart. `tests/test_toolset.py` asserts the
  table encodes exactly the promised set, the mapping is bijective with
  well-formed placeholder-free symbols, emission is deterministic, the emitted
  fragment round-trips to the committed file, and the table is honest that
  `fzf`/`starship` have no Buildroot package as of 2026-09-18 (a follow-up item
  tracks this).
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, stdlib-only at test time), the pytest harness, and
  the GitHub-ready docs.
- `.gitignore`, `README.md`, `CHANGELOG.md`, and `LICENSE` (MIT, Copyright
  (c) 2026 Pat Wendorf) so the repo is GitHub-ready from the first commit.
- `tests/conftest.py` (adds the tool root to `sys.path` so a bare
  `python3 -m pytest -q` works without `pip install -e .` — the verifier runs
  exactly that, cwd = `tools/pengyos-server-toolset`).
- `tests/test_scaffold.py` — package metadata, the pytest harness, and the
  deliverable directory structure (`fragments/`, `shell/`).
- `tests/test_github_ready.py` — the GitHub-ready contract: the four docs exist
  and are non-empty (and tracked), LICENSE is MIT with the exact copyright line,
  the README documents the tool set and cross-links `spec.md`/`implementation.md`,
  CHANGELOG is Keep-a-Changelog with its newest section equal to the project
  version, and `.gitignore` covers the Python build/test artifacts.
