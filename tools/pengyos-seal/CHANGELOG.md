# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **The documented `pengyos-seal run` (no `--*-bin` flags) was broken on the
  production runner** — two defects, both in `_require_runner`, that the
  injected-runner unit/integration tests could not see:
  1. It registered a `None` override for every absent `--*-bin` flag, so
     `FileSystemRunner._resolve` substituted `None` into `argv[0]` and
     `subprocess` raised `TypeError: expected str, bytes or os.PathLike object,
     not NoneType` before the tool ran. Absent flags are now omitted (run the
     tool by name from `PATH`), and `_resolve` treats a `None`/empty override as
     "no override".
  2. It built the runner without `build_dir`, so external tools ran with
     `cwd="."` and the build-relative sign argv (`images/rootfs.ext4`) resolved
     against the seal's own cwd — `pengyos-sign` failed with
     `signature I/O error: No such file or directory`. The runner now gets
     `build_dir=args.build`.

  Regression tests cover the previously-untested production path
  (`tests/test_cli.py::test_require_runner_omits_absent_bin_overrides`,
  `test_require_runner_uses_build_dir_as_runner_root`,
  `test_production_runner_runs_tool_by_name`,
  `tests/test_runner.py::test_filesystem_runner_ignores_none_override`).

### Added

- **GitHub-ready contract as a real gate** (`tests/test_github_ready.py`). The
  `test_github_ready` suite now enforces "docs in sync" in **both** directions:
  `test_readme_documents_only_real_subcommands` asserts every
  `pengyos-seal <sub>` usage line in the README names a subcommand the CLI
  actually registers (the existing `test_readme_documents_the_actual_cli_interface`
  only proved the one direction, actual → documented), and
  `test_readme_flags_are_real` asserts every `--flag` the README documents exists
  on the real parser (catching a documented-but-unimplemented or renamed flag).
  `_all_parser_options()` collects the option strings across the top-level parser
  and all subparsers so a documented `--build` on either subcommand is honoured.
  The gate is proven to catch drift: a fabricated `pengyos-seal frobnicate`
  usage line fails `test_readme_documents_only_real_subcommands`.

- **Opt-in (`PENGYOS_IT=1`) integration test** (`tests/test_integration.py`).
  `pengyos-seal run` now closes the provenance loop against a **real** build end
  to end: the test builds a controlled, sealable pengyos-build output dir (the
  two expected artifacts, a structurally valid SPDX + CycloneDX SBOM, and a repro
  report — the SPDX SBOM signed with the real `pengyos-sign` so the `sbom_signature`
  witness kind is real), runs `pengyos-seal run` against it with a real key via a
  `RealToolRunner` (a `FileSystemRunner` that execs the real tools), asserts the
  seal's closing gate is green (verifier exit 0), then runs `pengyos-verifier
  check --pub` and asserts the `sbom` + `signatures` findings PASS. The suite
  never needs the network, root, or a pre-existing build (a full pengyos-build
  tree is hundreds of MB of host toolchain); it **skips honestly** when the
  `pengyos-sign` release binary is not built and cleans up everything it creates.
  Dependency-free helpers (`test_seed_sboms_are_structurally_valid`,
  `test_seed_repro_report_is_valid`, `test_real_sign_bin_reports_absence_when_not_built`)
  pin the seed data and the skip predicate in the ordinary suite.
- **Machine-readable seal result** (`--json`): `run` now emits the full seal
  result as JSON via `--json OUT` (or to stdout with `--json` / `--json -`).
  `SealResult.to_dict()`/`to_json()` serialise the sealed plan, the artifacts
  signed, the names normalised to the verifier's conventional root names
  (`names.sbom` / `names.repro`), the repro status, and the verifier's
  per-check verdicts (`verifier.checks`), overall `verdict`, and `exit_code`.
  The per-check verdicts come from the verifier's own structured
  `{"checks": {...}}` stdout (`seal._verifier_checks`); the seal records only
  what the verifier reports and never invents a check. `--json` emits JSON
  only (the human stage lines are suppressed); the verifier's exit code still
  propagates as the seal's return code. `VERIFIER_CHECKS` documents the five
  canonical `pengyos-verifier check` gates.
- Deterministic **seal plan** model (`pengyos_seal/plan.py`). `plan` discovers
  the real artifact/SBOM names in `--build` by reusing the
  `pengyos-witness-bundle` alias convention (the build emits
  `images/<target>.spdx.json`; the witness reads `sbom.spdx.json`), then computes
  the ordered seal steps — `sign` → `name` → `repro` → `witness` → `verify` —
  with the exact argv each step would run. Discovery walks the build tree through
  the injected runner (`Runner.walk`), so it is fully testable against a virtual
  tree with no real files. The plan is deterministic and side-effect free;
  `plan --json` emits the machine view (discovered `target`/`sbom_source`/
  `repro_source`, the repro status, and each step's argv). The signing steps show
  a `<SEED>` placeholder (the key seed is a human boundary and `plan` takes no
  `--key`); `run` fills in the real seed. The repro status is derived
  deterministically from the tree + `--prev`: `existing` when a report is
  present, `diff` when `--prev` was given, else `skip` (a single build cannot
  compare itself).

- **Name stage** of the seal driver (`pengyos_seal/seal.py`). `run` now normalises
  the SBOM to the verifier's conventional root name: the build emits its SPDX
  document at the discovered path (`plan.sbom_source`, e.g. `images/m2.spdx.json`)
  and the witness/verifier read `sbom.spdx.json` at the build root, so the seal
  **symlinks** the former to the latter (a symlink, not a copy, for the ~100 KB
  doc — the canonical document stays the single source of truth and the build
  dir does not double its SBOM size). A build with no discovered SBOM is a
  recorded note (non-fatal), surfaced by the CLI, so the follow-up witness/verify
  stages surface that absence. The driver is now a `Seal` with `sign` +
  `normalise_sbom` stages called in provenance order from `Seal.run`; `SealResult`
  gains `sbom_symlinked` (the `(source, root)` build-relative paths, or `None`).
  `cmd_run` models the whole run with a deterministic `SealPlan` first (so the
  stages execute the exact argv the plan enumerated) and prints
  `normalised SBOM <source> -> sbom.spdx.json` on success. Idempotent: re-runs
  replace the symlink.

- **Sign stage** of the seal driver (`pengyos_seal/seal.py`). `run` now signs the
  expected artifacts through the injected runner: for each `EXPECTED_ARTIFACT`
  (`images/rootfs.ext4`, then `images/bzImage`) it probes the build tree and
  invokes `pengyos-sign sign <artifact> --key <seed> --sig <artifact>.sig`,
  producing the `pengyos-sig-v1` detached signature sibling. The seal never
  reimplements ed25519 — it always calls `pengyos-sign` so the convention stays
  single-sourced. Signing is idempotent (re-run re-invokes `pengyos-sign`,
  overwriting the `.sig`). A missing artifact, or a non-zero `pengyos-sign`
  exit, is a clean `SealError` naming the first offending artifact (mapped by
  `main` to a one-line stderr message, exit 1). The driver is a `Seal` class
  (`sign` + `run`) with a `SealResult` (`build_dir`/`signed`/`errors`) and a
  `run_seal` convenience wrapper; the remaining stages (`name`/`repro`/
  `witness`/`verify`) are wired in by follow-up items. `FileSystemRunner` gains a
  `build_dir` so relative paths and command cwd resolve against the build dir
  (absolute paths are unchanged).

- **Repro stage** of the seal driver (`pengyos_seal/seal.py`). `run` now surfaces
  the reproducibility report at the verifier's conventional root name
  (`repro-report.json`): the verifier reads `repro-report.json` at the build
  root, so the seal symlinks the discovered report (`plan.repro_source`, e.g.
  `images/repro-report.json`) to the root — a symlink, not a copy. The outcome is
  deterministic from the plan (`plan.repro_status`): `existing` (a report was
  discovered; left in place if already at the root, symlinked otherwise),
  `diff` (no report but `--prev` was given, so `pengyos-repro-report diff --a
  <build> --b <prev>` runs and its result — re-discovered, so it lands wherever
  the tool writes — is symlinked to the root), or `skip` (no report and no
  `--prev`: a single build cannot compare itself, a recorded non-fatal note). A
  diff that runs but exits non-zero, or produces nothing discoverable, is a clean
  `SealError` — the seal never masks a failed diff as a clean skip. `SealResult`
  gains `repro` (`ReproResult` with `status`/`symlinked`); `SealPlan` gains
  `repro_argv` (the diff argv when `diff`, else `None`); `cmd_run` prints
  `repro <source> -> repro-report.json` (or the already-at-root / skip note) and
  now threads `--prev` into the plan so a `--prev`'d run sees the `diff` status.

- **Witness + verify stages** of the seal driver (`pengyos_seal/seal.py`), closing
  US-CORE-6. `run` now builds the witness layout via
  `pengyos-witness-bundle run --build DIR [--pub PUB]` and runs
  `pengyos-verifier check --build DIR [--attest LOG] [--pub PUB]` — the closing
  gate. The witness stage runs the plan's `witness_argv`; a non-zero exit is a
  clean `SealError` naming the tool + stderr (a failed witness never reaches the
  verifier). The verifier's exit code *is* the verdict (0 = PASS, any non-zero =
  FAIL): the seal surfaces the verdict (`verifier PASS` / `verifier FAIL`) and
  returns the verifier's own exit code as the seal's return code (never raises on
  a failing verifier, so the verifier's code propagates). The verdict is parsed
  from a structured `{"verdict": ...}` stdout summary when present (rich verdict
  for the machine view), otherwise derived from the exit code, which is the
  single source of truth. `SealResult` gains `witness` (`WitnessResult` with
  `status`/`argv`) and `verifier` (`VerifierResult` with `exit_code`/`verdict`/
  `stdout`); `SealPlan` gains `witness_argv`/`verify_argv`. `cmd_run` threads
  `--pub`/`--attest` into `build_plan` so the witness/verify argv carry them (a
  sealed run verifies against the same key it witnessed with) and returns
  `verifier.exit_code`.

## [0.1.0] - 2026-09-19

### Added

- Scaffold of the `pengyos-seal` CLI with `plan` and `run` subcommands
  (argument parsing + dispatch) and the injected runner seam
  (`pengyos_seal/runner.py`). The seal is the closing glue for US-CORE-6: given
  a pengyos-build output dir + a signing key it signs the expected artifacts,
  normalises the SBOM to the verifier's conventional root name, surfaces the
  repro report, builds the witness layout, and runs `pengyos-verifier` — every
  external tool call behind an injected runner. The runner seam ships two
  implementations: `FileSystemRunner` (production: real files/symlinks/commands
  via `subprocess`, with per-tool binary overrides) and `MemoryRunner` (a fully
  in-memory, recording runner so the suite needs no subprocess, real
  filesystem, or root). The CLI validates `--build` is a directory and maps the
  single `SealError` to a clean one-line stderr message and exit 1 (no
  traceback). The expected artifacts, SBOM root name, repro report name, and
  signature suffix are pinned to the `pengyos-verifier` check definitions so the
  producer and the witness cannot drift.
- GitHub-ready project layout: `README.md`, `LICENSE` (MIT), `CHANGELOG.md`,
  `.gitignore` and `pyproject.toml` (declaring the `pengyos-seal` console
  script).
- pytest suite including `test_github_ready` and CLI/runner smoke tests.
