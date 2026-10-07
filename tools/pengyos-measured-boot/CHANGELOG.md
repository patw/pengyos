# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GitHub-ready contract (`tests/test_github_ready.py`): the suite now asserts the
  full contract from `spec.md` — the required files are present, `LICENSE` is MIT
  and carries "Copyright (c) 2026 Pat Wendorf", and the README is authored. It
  also keeps the docs in sync: the README names the tool and both subcommands,
  the README's license line matches the `LICENSE` file, the latest versioned
  `CHANGELOG.md` entry matches `pyproject.toml`'s version, `pyproject.toml`'s
  version matches the runtime package `__version__`, and the test suite is
  non-empty. Previously the contract only checked file presence.

- Measured-boot `run` backend (`pengyos_measured_boot/boot.py`): `run_measured_boot()`
  wires the full flow — boot the image under swtpm, read the PCRs, parse them,
  and verify them against the values required by `--expect`. The `run` subcommand
  is now fully wired (previously raised `CommandNotReady`); it emits a JSON
  result on success and a specific `PolicyMismatch` (exit code 2) on any
  mismatch. Backs the orchestration with `Policy.from_mapping()`, a classmethod
  that builds a `Policy` from an in-memory `{index: value}` mapping (the parsed
  `--expect` values), rejecting an empty policy.

- Policy verification (`pengyos_measured_boot/policy.py`): a `Policy` container
  (`{index: value}` integers) plus `parse_policy()` / `load_policy()`. The policy
  is a TOML `[expected]` table (or top-level keys) mapping PCR index → expected
  value, written as hex (`0x`-prefixed) or decimal to mirror `--expect`.
  `Policy.check()` returns the list of `Mismatch`es (value mismatch or a
  required PCR that was not measured); `Policy.verify()` raises
  `PolicyMismatch` — naming every mismatching PCR with expected vs. actual — on
  any mismatch. The `verify` subcommand now loads `--pcrs` + `--policy` and
  verifies them, emitting a JSON result on success and a clean, specific error
  (exit code 2) on failure. `PolicyMismatch` and `Mismatch` live in
  `errors.py`. Committed fixtures `pcrs.json` (good) and `pcrs-tampered.json`
  back the `verify` tests.

- PCR read/parse (`pengyos_measured_boot/pcrs.py`): a `Pcrs` container
  (`{index: value}` integers) plus `parse_pcrs()` / `parse_pcrs_file()`. The
  parser reads the live read-step stdout (`tpm2_getpcrs --format json`) and the
  committed fixture format, preferring the requested algorithm's digest and
  raising actionable errors on malformed input. `Pcrs.to_json()` serialises to
  the fixture format (`pcrN` keys, 64-char sha256 hex) for round-tripping into
  `pcrs.json`.

- Committed boot fixtures: `pcrs.json` (good) and `pcrs-tampered.json`
  (one PCR flipped) at the repo root. `pcrs.json` matches `policy.toml`;
  `pcrs-tampered.json` differs from it in exactly one required PCR, so it
  exercises the failure path without widening it. Backed by
  `tests/test_fixtures.py`, which asserts both fixtures parse, the good one
  matches the policy, the tampered one fails naming the PCR, and the tampered
  one differs from the good one in exactly the policy-named PCR.

- swtpm runner abstraction (`pengyos_measured_boot/swtpm.py`): a mockable
  `SwtpmRunner` whose subprocess layer is injectable (`ProcRunner`), plus
  golden-assertable `build_boot_argv` / `build_read_pcrs_argv` builders and a
  `require_tool` helper. No real `swtpm`, root, or hardware is needed — the
  subprocess layer is faked in tests.

## [0.1.0] — 2026-09-19

### Added

- Scaffold the repository: `pyproject.toml`, the `pengyos_measured_boot`
  package (CLI, errors), GitHub-ready files (`README.md`, `LICENSE`,
  `CHANGELOG.md`, `.gitignore`), and a green standard-library-only test suite.
- CLI `pengyos-measured-boot` with `run` and `verify` subcommands and
  `--expect PCR=VALUE` parsing (hex/decimal).
- `test_github_ready` asserting the GitHub-ready contract.

### Notes

- The `run`/`verify` backends are not yet wired; they raise a clear
  "not ready" error until the swtpm-runner and policy-verification tasks land.
