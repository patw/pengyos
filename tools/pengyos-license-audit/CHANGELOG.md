# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GitHub-ready contract (`test_github_ready`): pins the files and hygiene a
  fresh clone needs — `README.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat
  Wendorf"), `CHANGELOG.md`, `.gitignore`, a valid `pyproject.toml`, a cleanly
  importable package, a present test suite, and `pyproject`/`__version__` in
  sync. Suite: 107 green (105 + 2 new).

- Emit `LICENSES.md` (grouped by claimed license) and `report.json` with
  per-component **pass/warn/fail** verdicts:
  - `_render_licenses` now annotates each component line with its classified
    family and verdict in the shape `[<verdict>, family=<family>]`, and renders
    the loaded policy (allowed families, core-vs-copyleft posture, exceptions)
    under a `## Policy` heading. Empty manifests render a friendly placeholder.
  - `_build_report` populates the previously-reserved `verdicts` key in
    `report.json` — a per-component list (`component_id`/`family`/`verdict`/
    `reason`) plus a `summary.verdicts` tally — so the report is always
    self-consistent with the evaluated policy.
  - Tests for the grouped/stable `LICENSES.md` ordering with per-component
    verdict annotations, the Policy-section rendering, and the
    `report.json` verdicts schema (5 new tests; suite is 105 green).

- Per-component policy evaluation (`pengyos_license_audit.policy_eval`):
  - `evaluate(pins, policy) -> list[VerdictResult]` — decides pass/warn/fail for
    every component. `pass` = allowed outright (declared allowed family,
    copyleft permitted in core, or documented exception); `warn` = flagged
    (a copyleft claim in the core while `copyleft_in_core` is false and the
    component is only *dynamically* linked); `fail` = hard violation (a copyleft
    claim *statically* linked into the core, a proprietary/unknown claim in the
    core, a disallowed copyleft leaf, or any other disallowed family).
  - The **static-vs-dynamic link flag** (`Pin.link`) is the deciding factor for a
    copyleft-in-core claim: dynamic linking → `warn`, static linking → `fail`.
    A malformed claim is reported as a hard `fail`. `has_violations()` reports
    whether any component failed.
  - `Verdict` (pass/warn/fail) and `VerdictResult` added to `model.py` and
    exported from the package root.
- CLI `run` now annotates each component in `LICENSES.md` with its family and
  verdict, populates the previously-reserved `verdicts` key in `report.json`
  (per-component `component_id`/`family`/`verdict`/`reason` plus a
  `summary.verdicts` tally), and **exits non-zero (code 1) when the policy is
  violated without an exception**.
- Tests for the evaluation (link-aware warn/fail, exceptions, allowed-prefix
  matching, malformed claims, `has_violations`) and CLI exit codes (24 new
  tests; suite is 100 green).

- SPDX-ish license expression parsing + family classification
  (`pengyos_license_audit.classification`):
  - `classify(claim)` — normalises a raw license claim (trim, collapse
    whitespace, drop a trailing parenthetical note) and classifies it into a
    `LicenseFamily`.
  - `classify_expression(claim)` / `parse_expression(claim)` — parse and
    classify full SPDX expressions: a single id, an optional `WITH <exception>`
    clause, and `OR`/`AND` combinations with parentheses for grouping. `OR`
    folds to the freest family, `AND` to the least free.
  - The SPDX-id → family mapping (permissive / weak-copyleft / strong-copyleft /
    proprietary / unknown) with case-insensitive lookup; unmapped-but-well-formed
    claims map to `unknown`, malformed input raises `ValidationError`.
  - Exported `classify` / `classify_expression` from the package root.
- Tests for the parser, classifier, combination semantics, mapping table, and
  malformed-input handling (42 new tests; suite is 76 green).

## [0.2.0] — 2026-09-14

### Added

- License family taxonomy (`pengyos_license_audit.families`): the canonical
  `LicenseFamily` vocabulary (permissive / weak-copyleft / strong-copyleft /
  proprietary / unknown) plus a stable freedom ordering (`FAMILY_ORDER`).
- Refined the policy schema: `Policy` gains `is_exception()` / `exception_ids`
  helpers and a documented three-axis schema (allowed families, core-vs-leaf
  copyleft rule, exceptions). `load_policy` now trims, rejects empties, and
  forbids duplicates in `allowed_families`/`exceptions`, and requires
  `copyleft_in_core` to be a real boolean.
- Tests for the taxonomy and the policy schema (15 new tests; suite is 34 green).

### Changed

- `report.json` schema is unchanged; the reserved `verdicts` key (populated by
  the policy-evaluation subtask) remains reserved.

## [0.1.0] — 2026-09-14

### Added

- Scaffold the repository: GitHub-ready layout (README, LICENSE (MIT),
  CHANGELOG, `.gitignore`, `pyproject.toml`).
- Standard-library-only Python package `pengyos_license_audit` with a working
  CLI (`run` subcommand; `--pins`, `--policy`, `--out`, `--json`).
- Data model for the baseline manifest/policy schema (`Pin`, `Policy`) with
  validation.
- TOML/JSON I/O helpers.
- `LICENSES.md` rendering (components grouped by claimed license, stable order)
  and a stable `report.json` schema.
- Offline test suite (19 tests) covering model loading/validation, CLI exit
  codes, output shape, and `python -m` invocation.

[0.1.0]: https://github.com/patw/pengyos/tree/main/tools/pengyos-license-audit
