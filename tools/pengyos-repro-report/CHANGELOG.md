# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `--strict` exit code on `diff`/`score`: with `--strict`, the CLI exits
  non-zero (a clean `ReproReportError` -> exit 1, no traceback) whenever the
  reproducibility score is below 1.0, so CI can gate on reproducibility. The
  report is still written to the witness path before the strict check fails, so
  the witness can read it even when the run is a failure. `--strict` only fails
  on a *real* difference (a `normalized` difference still scores 1.0 and
  passes); without `--strict` both subcommands keep returning 0. Threaded
  through `cmd_diff`/`cmd_score` via a new `cli._enforce_strict`, and pinned in
  `tests/test_cli.py` (9 new tests).
- `test_github_ready` now enforces the GitHub-ready contract as a real gate, not
  just a file-exists smoke test. Beyond the scaffold (README/LICENSE/CHANGELOG/
  `.gitignore`/`pyproject.toml`, importable package, non-empty suite) it asserts
  the docs are *in sync* with the code: the README documents every subcommand
  the CLI actually exposes (discovered from the parser, not hard-coded) in a
  usage line, the README and LICENSE carry the "Copyright (c) 2026 Pat Wendorf"
  line, the CHANGELOG has a versioned or Unreleased section, and the pyproject
  version is semver and recorded in the CHANGELOG. The argparse subcommand
  discovery resolves portably across 3.9-3.14 (3.14 dropped `get_actions` and
  changed what `add_subparsers` returns) via the stable `_actions` accessor.
- Scaffold of the `pengyos-repro-report` CLI with `diff` and `score`
  subcommands (argument parsing + dispatch).
- Core rebuild-and-compare engine (`pengyos_repro_report/core.py`): SHA-256
  content hashing of two build dirs, a `Comparison`/`Artifact` model, canonical
  report construction, deterministic artifact ordering, and report
  write/load/round-trip helpers.
- Canonical `repro-report.json` schema (pinned `schema` version, `score`,
  per-artifact `status`/`hash_a`/`hash_b`/`normalized`, and a `summary`).
- GitHub-ready project layout: `README.md`, `LICENSE` (MIT), `CHANGELOG.md`,
  `.gitignore` and `pyproject.toml`.
- pytest suite including `test_github_ready` and CLI/core smoke tests.
- `build-manifest.json` consumption: each build input is resolved to a
  `{artifact_name: sha256}` mapping via `core.resolve_source` — the primary
  source is pengyos-build's `build-manifest.json` (artifact name -> sha256),
  falling back to a content-hash walk of the build's `images/` directory. The
  `diff` subcommand now accepts two build dirs OR two `build-manifest.json`
  files. `ReproReportError` moved to `core` so both layers raise a single
  clean-error type.
- Canonical `repro-report.json` schema pinned by a golden test and validated by
  a parse-back round-trip (`tests/test_report.py`): a fixed top-level shape
  (`schema` / `score` / `artifacts` / `summary`), one artifact entry per
  artifact (deterministically sorted by name, each with `name`, `status`,
  per-side `hash_a`/`hash_b`, and a `normalized` flag that is `None` for a
  byte-identical artifact and a bool only for a differing one), and a `summary`
  with `total`, `matching`, and a `counts` map. `write_report`/`load_report`
  round-trip deep-equal so the on-disk JSON the witness reads faithfully
  reproduces the in-memory report. The `counts` buckets the *final* statuses
  (`differs` is an intermediate state the classification table resolves onto
  `normalized`/`real` before the report is built, so it never appears here).
- Conventional witness-path emission: `diff` now writes to the path the witness
  reads **without an explicit `--out`**. With no `--out`, the report is written
  to the conventional witness path `<build>/images/repro-report.json` derived
  from `--a` (via the new `core.witness_path`); an explicit `--out` still wins.
  A non-directory `--a` (a `build-manifest.json` file, or a missing path) has no
  conventional witness path, so `--out` is then required and its omission is a
  clean non-zero exit (no traceback). `diff`/`score` now round-trip through this
  exact path.
- Data-driven difference classification (`pengyos_repro_report/classify.py`):
  each differing artifact is classified as `normalized` (differs only in a
  known non-deterministic field — recorded build timestamp, unix timestamp,
  tar mtime, or build-id) or `real` (a genuine reproducibility failure). The
  rule table is pluggable (`DEFAULT_RULES`); a rule blanks its field from both
  payloads and the first rule whose blanked residuals match fully explains the
  difference. `core.compare_dirs`/`compare_sources` apply it in-place; a
  `normalized` artifact still counts toward the score, a `real` one does not.
  Manifest-only inputs (no raw bytes reachable) classify differing digests as
  `real`. `summarize` and the report `summary.counts` now report a `normalized`
  bucket.
- Clean, non-zero exit (no traceback) for missing/unreadable manifests, dirs,
  and reports. `core.load_manifest` now catches `OSError` (unreadable file, a
  path that is a directory, permission denied) alongside `json.JSONDecodeError`
  and raises a single `ReproReportError`; `core.load_report` does the same for
  the `score` path (missing/unreadable/malformed report). `core.walk_images`
  passes an `onerror` handler to `os.walk` so an existing-but-unreadable
  `images/` directory raises a clean `ReproReportError` instead of silently
  returning an empty mapping (which produced a wrong all-`identical` score).
  The CLI's `main` now maps the single `ReproReportError` to exit 1 with a
  one-line message and dropped its now-dead `FileNotFoundError` handler (the
  report path goes through `load_report`, which converts that error). New
  `tests/test_errors.py` pins every path (13 tests).

## [0.1.0] - 2026-09-18

### Added

- Initial scaffold of the `pengyos-repro-report` tool.
