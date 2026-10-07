# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--json` output for every subcommand. A std-only JSON emitter
  (`src/json.rs`) serializes the same [`DispatchOutcome`] two ways: the human
  report in text mode, or a stable machine-readable document when `--json` is
  present anywhere in `argv`. The document carries the subcommand, the resolved
  tool, the forwarded argv, the exit code, the captured stdout/stderr, and an
  `ok` flag (`exit_code == 0`); `doctor` additionally embeds its readiness
  matrix under a `checks` array, so callers can always read `exit_code` / `ok`
  uniformly. `--json` is a *global* flag consumed by the meta-CLI and never
  forwarded to the sibling tool (it is stripped before parsing), so no
  subcommand spec has to declare it and the tool's raw stderr is captured
  verbatim in the document (no text-mode annotation is added in JSON mode).
  Pinned by unit tests (`json`, `dispatch`, `doctor`) and an end-to-end
  integration test (`tests/dispatch.rs`).
- Subcommand framework (clap-style, standard-library only): a declarative
  `ArgSpec` argument parser (`src/args.rs`) supporting long/short flags and
  options, `--help` short-circuiting, `--` and short-flag bundling, with
  deterministic, unit-tested parsing.
- Config loading (`src/config.rs`): a minimal, std-only TOML parser (tables,
  scalar and array values, quoted strings with escapes, comments) with typed
  accessors and `tool_for` resolution. Config is found via `$PENGYOS_CONFIG`,
  else `$XDG_CONFIG_HOME/pengyos/config.toml` / `~/.config/pengyos/config.toml`;
  a missing file yields an empty config.
- Dispatch framework (`src/dispatch.rs`): a `Registry` of registered
  subcommands, an injectable `Runner` trait (real `ProcessRunner` + test
  `MockRunner`), and a generic `forward_handler` that forwards parsed arguments
  to each subcommand's sibling tool. `main.rs` now resolves config and dispatches
  through the registry.
- `doctor` subcommand (`src/doctor.rs`): a native host-readiness check that
  evaluates a fixed matrix — sibling **tool presence** on `PATH`, `/dev/kvm`
  availability, and the **Buildroot pin** (`YYYY.MM[.N]`) — and prints a
  scriptable readiness report. Exit code `0` when every required check passes,
  `1` when a required check fails (optional warnings such as a missing KVM are
  fine). The checks run against an injectable [`HostProbe`] (fixture [`Host`] in
  tests, real `SystemProbe` in the binary), so the logic is unit-tested without
  touching the network, root, display, or hardware.
- Integration tests (`tests/dispatch.rs`) for the binary's dispatch surface.
- Dispatch to the sibling tools with **clear error surfacing**: when a forwarded
  tool exits non-zero, `pengyos` prepends a one-line annotation to stderr naming
  the subcommand, the resolved tool, and the exit status
  (`pengyos build: `pengyos-build` exited with status 1`), so a failure is
  visible even when the tool itself is quiet. The tool's own stdout/stderr pass
  through untouched, and a successful run is left unchanged. Coverage: argv/dispatch
  golden per subcommand (`build`, `verify`, `triage`, `profile`, `sbom`) and the
  error-surfacing contract, all behind the mock `Runner` (unit) plus an
  end-to-end check through the real binary (integration).

## [0.1.0] - 2026-09-18

### Added

- Crate scaffold for `pengyos-cli`: a `pengyos_cli` library plus the `pengyos`
  CLI binary, built from a single package.
- The `pengyos` binary entry point: `--version` / `-V`, `--help` / `-h`, and a
  clear usage error (exit code `2`) for a missing or unknown command.
- GitHub-ready contract: MIT LICENSE (Copyright (c) 2026 Pat Wendorf), authored
  README, Keep-a-Changelog `CHANGELOG.md`, and `.gitignore`.
- Rust test suite: unit tests for the library (version shape, the documented
  subcommand surface) and integration tests for the binary's trivial surface and
  the GitHub-ready contract (`tests/version.rs`, `tests/github_ready.rs`).

### Work planned (separate subtasks)

- Subcommand framework (clap-style UI) + config loading.
- `doctor` that reports host readiness (tool presence, /dev/kvm, Buildroot pin).
- Dispatch to the sibling Python tools with clear error surfacing (mocked in
  tests).
