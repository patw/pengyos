# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-09-25

### Added
- The live-user sudo policy (implementation.md item 5): a sudoers drop-in letting
  the `live` user run `pengyos-install` without a password, so the installer can
  autostart on the live session and complete the install (partition, format,
  mount, copy) without a human typing a password.
  - `pengyos_live/model.py`: `SUDOERS_PATH = "/etc/sudoers.d/pengyos-live"` and
    `_sudoers_content()` — the line
    `live ALL=(ALL) NOPASSWD: /usr/bin/pengyos-install` — appended to every
    overlay (session-independent, so both the `console` unit and the `cosmic`
    entry get it). `LiveOverlay.sudoers_file` and the JSON schema `minItems`
    (3 → 4) follow.
  - `emit` writes it automatically (the write path already iterates
    `overlay.files`, so no emit change was needed); every emitted overlay now
    carries four files (autologin, live marker, installer autostart, sudo
    policy).
  - `lint` rule 5 — `sudo_policy_present` — the drop-in exists and names the
    live user, is `NOPASSWD`, and is scoped to the `pengyos-install` binary.
    Every rule now has teeth (one mutation test each).
- `tests/`: model coverage (`sudoers_file` present for both sessions, 4 files
  per overlay, sudoers content), emit coverage (sudo policy written for both
  sessions, last-written order), and lint coverage (missing / no-`NOPASSWD` /
  wrong-binary mutations). Golden overlays for all six edition × session combos
  regenerated with the sudoers file.

## [0.4.0] - 2026-09-25

### Added
- `lint` — check an emitted live overlay against every live-overlay rule
  (implementation.md item 4): `pengyos_live/lint.py`.
  - `lint_directory(overlay_dir)` reads the flattened overlay `emit` wrote back
    into a `{absolute_target_path: content}` map (`read_overlay`, the mirror
    image of `emit.emit_path`), and `lint_overlay(overlay)` runs the four rules.
  - **Rules, every one with teeth:**
    - *installer present AND wired to autostart for the chosen session* — the
      overlay ships exactly one installer autostart artifact (the `console`
      systemd unit or the `cosmic` desktop entry), it references the
      `pengyos-install` binary, and it is actually wired to autostart
      (`WantedBy=multi-user.target` for the unit,
      `X-GNOME-Autostart-enabled=true` for the entry). The session is *inferred*
      from which artifact is present, so an overlay shipping the wrong artifact
      (or both/neither) fails.
    - *autologin present* — the getty autologin drop-in exists and
      `--autologin live`.
    - *live marker present* — `/etc/pengyos/live` exists and sets `live=true`.
    - *no pre-emptive disk write* — the installer autostart launches
      `pengyos-install` without a `dd`/`mkfs`/partitioning/`of=/dev/` command or
      a `--non-interactive`/`--yes`/`--force` bypass flag.
  - `LintReport` (`ok`, inferred `session`, `findings`), `LintFinding`
    (`rule` + `detail`), `LintRule`, and stable `report_to_json` (`ok`,
    `session`, `errors`).
  - `pengyos-live lint` now lints the overlay and returns `EXIT_OK` (0) for a
    clean overlay or `EXIT_FAIL` (1) when any rule is violated (the scaffold
    returned the reserved "not implemented" code); `--json` prints the report,
    otherwise a human summary (or, on failure, each finding on stderr). A
    missing/unreadable overlay directory is `EXIT_FAIL`.
- `tests/test_lint.py`: `read_overlay`, clean-overlay pass for all six edition ×
  session combos, a mutation test for every rule (including one per
  pre-emptive-disk-write pattern), the report/`--json` shape, and the CLI
  end-to-end behaviour. The strongest check links emit and lint: every emitted
  golden overlay lints clean.
- Removed `test_scaffold.py::test_lint_not_implemented_at_scaffold` (lint is no
  longer not-implemented).

## [0.3.0] - 2026-09-25

### Added
- `emit` — render the live overlay to disk (implementation.md item 3):
  - `pengyos_live/emit.py`, the emit module. `write_overlay(overlay, out_dir)`
    writes every `LiveOverlay.files` file under `out_dir`, flattening each
    absolute target path (e.g. `/etc/pengyos/live`) relative to the output root
    by stripping the leading `/` and creating parent directories — the emitted
    tree mirrors the target's `/etc` + `/usr` layout. `emit_from_strings`
    builds the overlay from canonical edition/session strings and writes it
    (raising `ValueError` for an unknown edition/session, mirroring the model).
    `summary_from_written` / `summary_to_json` give a stable, sorted-keys JSON
    summary (edition, session, files, written paths) for `--json`.
  - `pengyos-live emit` now writes the overlay and returns `EXIT_OK` (0)
    instead of the scaffold's reserved "not implemented" code; `--json` prints
    the summary, otherwise a human-readable file list.
  - Committed golden oracle under `tests/golden/<edition>-<session>/`
    (all six edition × session combos) used to assert the emitted tree is
    byte-for-byte correct.
- `tests/test_emit.py`: path flattening, directory creation, overwrite, write
  order, per-session file selection, determinism, the golden comparison for
  desktop + cosmic and desktop + console, the `--json` summary shape, and the
  CLI end-to-end behaviour.

## [0.2.0] - 2026-09-25

### Added
- The live-overlay data model (implementation.md item 2): `pengyos_live/model.py`.
  - `Edition` (`desktop`/`core`/`server`) and `Session` (`cosmic`/`console`)
    enums whose `value` is the canonical string used in the emitted overlay and
    the JSON schema; `Session` also carries `is_graphical`/`is_console`.
  - `OverlayFile` (frozen `path` + `content`) and `LiveOverlay` (edition +
    session + an ordered, deterministic `files` tuple) — the whole overlay as a
    single structured value.
  - `build_overlay(edition, session)` — the single source of truth for what an
    emitted overlay contains: the getty autologin drop-in, the live marker, and
    the installer autostart (a `pengyos-installer.service` systemd unit for
    `console`, a `pengyos-installer.desktop` autostart entry for `cosmic`).
  - The `OVERLAY_JSON_SCHEMA` plus `overlay_to_dict`/`overlay_to_json` for a
    stable, machine-checkable JSON representation (used later by `lint`).
- `tests/test_model.py`: enum values/membership, installer wiring per session,
  autologin+marker presence, full edition × session coverage with unique paths,
  per-component content, determinism, `LiveOverlay` properties,
  `build_overlay_from_strings`, and the JSON schema + round-trip serialisation.

## [0.1.0] - 2026-09-25

### Added
- Project scaffold (implementation.md item 1): `pyproject.toml` (PEP 621, MIT,
  `setuptools` build backend, stdlib-only), the `pengyos_live` package, and the
  `pengyos-live` console script plus a `python -m pengyos_live` entry point.
- CLI argument surface (implementation.md item 1): `--version` plus the two
  documented subcommands — `emit` (`--edition desktop|core|server --session
  cosmic|console --out DIR [--json]`) and `lint` (`--overlay DIR [--json]`) —
  with the flags, defaults, and choices in `spec.md`. The subcommand *logic* is
  tracked as its own subtasks in `implementation.md` and currently returns a
  clear `not implemented yet` message with a distinct exit code (`3`);
  `--version` and no-arg help are real and working.
- `tests/test_scaffold.py`: package metadata, entry-point behaviour, the CLI
  argument surface, the declared entry point, and the scaffold "not yet
  implemented" exit code for `emit`/`lint`.
- `tests/test_github_ready.py`: the GitHub-ready contract is machine-checked
  (README/LICENSE/CHANGELOG/.gitignore present and non-empty; MIT "Copyright
  (c) 2026 Pat Wendorf"; README documents every subcommand and cross-links
  `spec.md`/`implementation.md`; CHANGELOG newest section equals the package
  version; `.gitignore` covers the Python build/test artifacts; the full-suite
  green check is an opt-in `PENGYOS_IT=1` subprocess re-run).
- `README.md`, `CHANGELOG.md`, `LICENSE` (MIT, Copyright (c) 2026 Pat Wendorf),
  and `.gitignore`.
