# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `pengyos_kernel_fragments/fragments.py`: the authoritative definition of the
  kernel config **fragment file format** (`CONFIG_X=y` / `=m` / `="s"`, plus the
  tolerant `n` form) and its parser. `parse_fragment_text` /
  `parse_fragment_file` skip comments, blank lines and surrounding whitespace,
  strip inline comments while preserving `#` inside quoted strings, classify
  values into `ValueKind` (`y`/`n`/`m`/`s`), and record malformed lines as
  `ParseErrorRecord`s rather than raising. `load_directory` loads every
  `*.fragment` file in a tree (sorted, ignoring non-fragment files).
- `tests/test_fragments.py`: 27 tests covering the format, value kinds,
  tolerance, intra-file last-wins precedence and directory loading.

- `tests/test_repo.py::test_github_ready`: the unified GitHub-ready contract
  assertion (spec.md Verification). It verifies README.md (authored, links
  the license), LICENSE (MIT + the exact "Copyright (c) 2026 Pat Wendorf"
  line), CHANGELOG.md (authored with the 0.1.0 section), and .gitignore, then
  runs the full test suite in a fresh subprocess and asserts it is green. The
  sub-run deselects `test_github_ready` itself (via `-k`) to avoid infinite
  recursion; a non-zero return code (any failure, skip, or collection error)
  fails the check with a tail of the pytest output for debuggability. The
  pre-existing per-file repo checks remain as focused unit tests.

- `pengyos_kernel_fragments/validate.py`: the `validate` command (implementation.md task 3). Three checks, in order — (1) **syntax**: every malformed line the tolerant parser records is surfaced as a `SYNTAX` error; (2) **conflicts**: the same `CONFIG_` symbol assigned different values (across fragments, or within one file) is reported as a `CONFLICT` with every `(source, value, line_no)` assignment; (3) **required symbols**: rules declared as data (:data:`REQUIRED_RULES`, currently `systemd` needs `CONFIG_CGROUPS`/`CONFIG_EPOLL`/`CONFIG_INOTIFY`) fire when a required symbol is absent or disabled (`=n`). Findings are sorted deterministically (severity, source, symbol, line). `ValidationReport` exposes `ok` / `exit_code` / `problems`; `select_rules()` filters rules by `--profile` (case-insensitive, unknown profile raises); `format_report()` renders the CLI output.
- `pengyos_kernel_fragments/compose.py`: the `compose` command (implementation.md task 4). A **profile** is a `*.profile` file that lists fragment basenames, one per line (blank lines / `#` comments ignored); profiles live alongside the fragments in the directory passed to `compose`. `compose_directory(dir, name)` loads the `*.fragment` library and the named profile, then `merge_fragments(order, library, profile)` merges in profile order with **later-wins** precedence: a later fragment that assigns a *different* value to an already-set symbol yields a `ConflictWarning` (the later value wins; compose proceeds rather than aborting). The merged set is emitted deterministically, sorted by symbol, in the same `CONFIG_X=value` line format the fragments use. `ComposeReport` exposes `order` / `merged` / `warnings` / `missing` / `profile_missing`; `ok` / `exit_code` (0 clean, 2 usage error). `render_merged` / `render_warnings` / `render_error` render each part so the CLI can send the CONFIG set to stdout (or `--out`) and warnings/errors to stderr.
- `pengyos_kernel_fragments/cli.py`: the `compose <dir> --profile NAME [--out PATH]` subcommand. Exit codes: `0` clean, `2` usage error (not a directory, missing profile, unresolved fragment, or a missing `--profile`).
- `tests/test_compose.py`: 24 tests covering profile parsing, merge + later-wins precedence, conflict warnings, same-value (no conflict), missing-fragment / missing-profile usage errors, deterministic sorted output, the `render_*` helpers, end-to-end directory composition, the committed `n100` profile, and the CLI (stdout / `--out` file / warnings-to-stderr / usage errors / required `--profile`).
- `fragments/n100.profile`: the Intel N100 reference profile (`qemu` + `systemd` + `intel-igpu`), used as a compose fixture and validated clean.
- `tests/test_validate.py`: 29 tests covering syntax surfacing, intra/inter-file conflict detection, the required-pairs rule (missing, disabled, custom rules), rule selection, deterministic ordering, report rendering, end-to-end directory loading, and the CLI (clean / problems / profile filter / usage errors).
- Committed fragment library in `fragments/`: `qemu.fragment`, `systemd.fragment`, `intel-igpu.fragment`, `amd-igpu.fragment`, `nvidia-proprietary.fragment` — used as fixtures and validated clean.
- `fragments/amdv1000.profile`: the AMD reference profile (`qemu` + `systemd` + `amd-igpu`), the AMD Ryzen Embedded V1000 (Vega APU) analog of `n100`. Composes clean with no conflict warnings.
- `fragments/nvidia.profile`: the NVIDIA reference profile (`qemu` + `systemd` + `nvidia-proprietary`), a QEMU-boot + systemd build driving a NVIDIA proprietary discrete GPU. Composes clean with no conflict warnings.
- `tests/test_compose.py`: tests for the shipped per-hardware profiles — each composes clean with no later-wins warnings, exposes one symbol from each of the three concerns (QEMU / systemd / GPU), references only fragments present in the library, and ships with documenting `#` comment lines; plus a CLI end-to-end compose of each.
- Profile format documented in the README ("Profiles" section: `*.profile` = list of fragment basenames, one per line, blanks / `#` comments ignored, profiles live alongside fragments, `qemu` / `qemu.fragment` both resolve) and in spec.md (the shipped-profile table).
- `pengyos_kernel_fragments/explain.py`: the `explain` command (implementation.md task 6). `explain_directory(dir, symbol, profile=None)` scans the `*.fragment` files in a directory and reports, for a single `CONFIG_` symbol, every fragment that assigns it (`Assignment(source, value, line_no)`) plus its **final value** via the same later-wins precedence as `compose` — over the directory's fragments sorted by name when no profile is given, or over the named profile's fragment order when `--profile` is supplied. `ExplainReport` exposes `ok` / `exit_code` (0 answerable, 2 usage error — a missing/unknown profile mirrors `compose`); `render_explain` renders the CLI output. A symbol no fragment sets is a clean answer (exit 0), not an error.
- `tests/test_explain.py`: 26 tests covering source tracing (single/multiple sources, line numbers, not-found, empty library), the profile-scoped merge (profile order drives the final value, scoping to profile fragments, missing/unknown-profile usage errors), `ExplainReport` exit codes, `render_explain` (not-found / single / multiple / profile header), the CLI (success / not-found-clean / with-profile / bad-symbol / missing-profile / not-a-directory / default `./fragments`), the parser wiring, and end-to-end traces against the committed library.

## [0.1.0] - 2026-09-14

### Added
- Initial repo scaffold: `pyproject.toml` (setuptools, stdlib-only runtime,
  `pengyos-kernel-fragments` console script), `pengyos_kernel_fragments/`
  package with an argparse CLI (`--help`, `--version`, `version` subcommand)
  and `__main__`.
- pytest harness (no network, no root; integration tests opt-in via
  `PENGYOS_IT=1`).
- README, MIT LICENSE, CHANGELOG, .gitignore.
