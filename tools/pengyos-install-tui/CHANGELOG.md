# Changelog

All notable changes to **pengyos-install-tui** are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/); the
project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Desktop images: "Log in automatically, skipping the login screen? [y/N]"
  (default no), shown in the summary.

### Added
- **GitHub-ready contract tests** (`tests/test_github_ready.py`, 11): the last
  checklist item — the contract that the repo is publishable as a clean public
  project at all times (spec.md). Extends the existing six artifact checks with
  five that close real gaps: the README must document the real CLI options
  (`--answers`, `--config`, `--json`); the LICENSE must be the *entire* canonical
  MIT clause chain (not just a few substrings, so a look-alike stub is caught);
  the CHANGELOG must carry its version footer links (`[Unreleased]:` + a
  versioned link); `pyproject.toml` must pin `requires-python = ">=3.11"` (the
  shipped stick runs 3.11); and the package `__version__` must equal the
  pyproject `version` (a classic drift foot-gun). All assertions are
  non-vacuous — each catches the exact regression it guards against. (11 tests;
  suite now 152 green, 15 integration opt-in skipped.)
- **End-to-end integration tests** (`tests/test_integration.py`): the *Ground*
  pass — the CLI is now exercised for real as a subprocess
  (`python -m pengyos_install_tui`), not just in-memory. Covers the happy path
  (`run --answers` and `auto --config` both emit the plan), a **back-navigation**
  (`back` at the edition step, re-answer, confirm the edited value wins), an
  **invalid input** (an interactive bad answer is reported to stderr and
  re-prompted; a `--answers`/`install.toml` with a bad field exits 1 with a clean
  message and never a traceback), and the spec contract **auto == interactive**
  (`auto --config install.toml --json` and `run --answers answers.json --json`
  emit byte-identical JSON for the same inputs, happy and full static/A-B
  variants). Opt-in and skipped unless `PENGYOS_IT=1` is set (spec.md / prompt.md):
  the suite stays green without it — this module only *collects* unless the
  operator turns the integration run on. Scratch files live under
  `/home/beholder/tmp` (disk), never the `/tmp` tmpfs. (15 tests.)
- **`run` interactive front-end** (`pengyos_install_tui.run`): drive the pure
  wizard on stdin/stdout — render each step (progress header, human title, valid
  values, current value), apply the answer read from stdin, advance the cursor,
  and render the final plan review (the `confirm` step asks for a final `yes`).
  A `back` command returns to the prior step (review / re-edit); an invalid
  answer is reported to stderr and re-prompted (never a crash); an EOF at a
  prompt ends the loop without a plan. Scriptable via `--answers` (a JSON answer
  set — the JSON twin of `install.toml`): :func:`run_replay` applies each field
  through the wizard and confirms, and :func:`load_answers` reads/parses the
  file (a missing/unreadable file, invalid JSON, or non-table top level raises
  `ConfigError`, a reported error, never a crash). The `run` CLI handler
  (`run [--answers answers.json] [--json]`) prints the plan (`--json` for the
  machine-readable form) and maps every error to exit code 1. The `run` and
  `auto` paths produce the *exact* same `Plan` (verified by
  `test_run_replay_equals_interactive` / `test_cli_run_json_matches_auto_plan`).
  Interactive rendering is over injectable streams (`InteractiveRunner`) so the
  whole thing is offline-testable. (40 tests in `tests/test_run.py`.)
- **`--auto` front-end** (`pengyos_install_tui.auto`): parse an `install.toml`
  into the *same* answer set the interactive `run` path produces — the spec's
  "auto(config) equals the interactive answer set for the same inputs". It reads
  and parses the TOML, applies each field through the wizard's step validators,
  and returns the confirmed :class:`~pengyos_install_tui.model.Plan` (the single
  source of truth, rendered by `Plan.to_dict` — so `auto` and `run --json` can
  never drift). A missing/invalid field is a **reported error, not a crash**: a
  missing/unreadable file, invalid TOML, or a non-table top level raises
  `ConfigError` (naming the field or path); a bad value raises the model's
  `ValidationError` (carrying `.message`); a missing required field raises
  `ConfigError` naming it. The CLI handler (`auto --config install.toml [--json]`)
  maps both error kinds to exit code 1 with a clean message. The five answerable
  keys (`target`, `edition`, `layout`, `user`, `network`) accept either a bare
  scalar or a mapping, matching what the interactive wizard stores. Block-device
  facts stay injected via the optional `probe`, so the parser is fully
  offline-testable. (29 tests in `tests/test_auto.py`.)
- **Wizard state machine** (`pengyos_install_tui.model`): the pure, offline-testable
  wizard that drives `pengyos-install` — six ordered steps
  (`target` → `edition` → `layout` → `user` → `network` → `confirm`), a
  validator per step (an invalid answer raises `ValidationError` with a message,
  never a crash), back-navigation (`back()`), and derived answers (`confirm()`
  returns a canonical :class:`Plan`, the exact answer set `auto` must reproduce).
  No I/O: block-device/size facts are injected via an optional `probe`. The six
  step keys, editions, and layout values are stable constants shared with the
  Rust GUI front-end. Exposed on the package surface
  (`from pengyos_install_tui import Wizard, Plan, StepKey`).
- **Repository scaffold**: `pengyos_install_tui/` package (`__init__`,
  `__main__`, `cli`) with the `pengyos-install-tui` CLI skeleton — `run` and
  `auto` subcommands with the full spec option surface (`run --answers`,
  `auto --config`, shared `--json`), the `ExitCode` convention
  (0 ok / 1 error / 2 usage / 3 not-yet-implemented), and `--version`. The
  `run` / `auto` handlers are documented placeholders that raise
  `NotYetImplementedError` (exit 3) pointing at the next task in
  `implementation.md`; the wizard engine and the `install.toml` parser land as
  separate tasks.
- `pyproject.toml` (stdlib only, console script `pengyos-install-tui`,
  `pytest` test extra, `pythonpath` configured for the test suite).
- `README.md`, `LICENSE` (MIT, `Copyright (c) 2026 Pat Wendorf`),
  `CHANGELOG.md`, `.gitignore`, and smoke tests covering package/CLI import,
  version ↔ pyproject agreement, the console-script target, parser/option
  wiring, unknown-option and missing-command refusals, the not-implemented
  exit code, and `python -m pengyos_install_tui`.

## [0.1.0]

### Added
- Initial scaffold (see `[Unreleased]` above, promoted on release).

[Unreleased]: https://github.com/patw/pengyos/tree/main/tools/pengyos-install-tui
[0.1.0]: https://github.com/patw/pengyos/tree/main/tools/pengyos-install-tui
