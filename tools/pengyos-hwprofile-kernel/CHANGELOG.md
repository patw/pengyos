# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Conventional Commits](https://www.conventionalcommits.org/).

## [Unreleased]

### Added

- **Device-class -> fragment mapping** (`pengyos_hwprofile_kernel.mapper`):
  `select_fragments(document)` maps a validated hwprofile document's device
  sections to kernel **fragment basenames** via a data-driven `MAPPING` table of
  `Rule(section, attribute, value) -> fragment` rows (adding a device class is a
  data edit, not a code change). Signals: cpu `vendor`; gpu `driver` (preferred)
  then human-readable `vendor` name; network `driver`; storage `bus`/`transport`.
  Devices matching no rule are **reported** in `MapperReport.unknown` and never
  dropped or raised (the "warn, never silently drop" requirement). Output is
  deduplicated and symbol-sorted (deterministic). `tests/test_mapper.py`
  (table-driven, 15 cases).

- **`select` command + bundled fragment library** (`pengyos_hwprofile_kernel.select`):
  `compose(document, fragments_dir)` maps a profile's devices to fragments and
  merges them with the sibling `pengyos-kernel-fragments` **later-wins** grammar
  (`merge_fragments`), surfacing conflict warnings and any selected fragment
  absent from the fragments dir. `render_defconfig` emits a deterministic,
  symbol-sorted `CONFIG_X=value` defconfig (trailing newline). The `select` CLI
  prints the defconfig to stdout (or `--out`), conflict + unmapped-device
  warnings to stderr, and exits 0 (ok) / 1 (bad profile) / 2 (missing fragment /
  usage error). Ships a self-contained `fragments/` directory (9 `*.fragment`
  files: the 5 reference fragments reproduced + new `amd-cpu`/`intel-cpu`/`nvme`/
  `sata` fragments). `tests/test_select.py` (15 cases).

- **`exclude` command + excluded-drivers report** (`pengyos_hwprofile_kernel.exclude`):
  `compute_excluded(document, fragments_dir)` reports the kernel symbols the
  generic/full kernel has that *this* machine does not. The **baseline** is the
  union of every `CONFIG_` symbol set by every fragment in the fragments
  directory (the "full"/generic image); the report is the set difference
  `baseline − selected`. A symbol is excluded when the machine's merged config
  does not *enable* it (`=y`/`=m` means carried; absent or `=n` means omitted).
  `baseline_symbols` builds the deterministic, symbol-sorted baseline (each
  symbol lists every providing fragment); `render_excluded` prints a header +
  one line per excluded symbol. The `exclude` CLI prints the report to stdout
  (or `--out`), unmapped-device warnings to stderr, and exits 0 (ok) / 1 (bad
  profile) / 2 (missing fragment / usage error). `tests/test_excluded.py`
  (22 cases).

- **`explain` command — trace a symbol to the device that pulled it in**
  (`pengyos_hwprofile_kernel.explain`): `explain(document, fragments_dir, symbol)`
  walks the profile → mapper → fragments → merge flow backwards, returning an
  `ExplainReport` listing the *selected* fragments that set the symbol (each with
  its value, line number and the device(s) that pulled the carrying fragment into
  the machine's image), plus the symbol's later-wins final value / source. A
  symbol no selected fragment sets is a clean not-found answer (`found` is
  `False`), never an error. The `explain` CLI (`cli.py _cmd_explain`) prints the
  report to stdout (or `--out`), unmapped-device warnings to stderr, and exits 0
  (ok) / 1 (bad profile) / 2 (missing fragment / usage error).
  `tests/test_explain.py` (21 cases).

- **`diff` command — fragment delta between two profiles = hardware-change
  detection** (`pengyos_hwprofile_kernel.diff`): `diff(document_a, document_b,
  fragments_dir)` maps both profiles to their selected fragment sets and reports
  the delta going `A -> B`: `added` = `fragments(B) - fragments(A)`, `removed` =
  `fragments(A) - fragments(B)`; unchanged fragments are not reported and each
  changed fragment carries the symbols it sets. The `diff` CLI
  (`cli.py _cmd_diff`) prints the delta to stdout (or `--out`), supports
  `--json`, unmapped-device warnings go to stderr, and it exits 0 (ok) / 1 (bad
  profile) / 2 (missing fragment / usage error). `tests/test_diff.py` (21 cases).
  Committed fixture `tests/fixtures/profile_intel.toml` (an Intel-desktop profile
  that differs from the AMD-laptop fixture only by CPU vendor + GPU driver, so
  the delta is exactly `amd-cpu/amd-igpu -> intel-cpu/intel-igpu`).

- **Profile loading + validation** (`pengyos_hwprofile_kernel.profiles`):
  `load_profile(path)` reads a `profile.toml` via `tomllib` and validates it
  against the shared `pengyos-hwprofile` schema, raising a single tool-level
  `ProfileError` for a missing file, malformed TOML, or schema violation
  (missing required field / wrong type / unknown section). Consumes the real
  hwprofile artifact (TOML). Committed fixture
  `tests/fixtures/profile.toml` (a real `pengyos-hwprofile detect` AMD-Ryzen
  laptop profile) plus malformed fixtures. `spec.md` updated to name
  `profile.toml` as the `--profile`/`--a`/`--b` argument.

- The **GitHub-ready contract** assertion (`tests/test_github_ready.py`), closing
  the `test_github_ready` subtask in `implementation.md`. It asserts, from
  committed repo files only (no network/root/hardware), that the project stays
  GitHub-ready: `README.md` and `CHANGELOG.md` are authored, `LICENSE` is MIT
  with "Copyright (c) 2026 Pat Wendorf", `.gitignore` ignores the generated
  defconfig fragment / excluded-drivers report, `pyproject.toml` is valid and
  carries the `pengyos-hwprofile-kernel` console entry point, and
  `pyproject.toml`'s version is in sync with `pengyos_hwprofile_kernel.__version__`.

## [0.1.0]

### Added

- The `pengyos-hwprofile-kernel` package scaffold: `pyproject.toml` with the
  `pengyos-hwprofile-kernel` console entry point, the `pengyos_hwprofile_kernel`
  package with a `--version`/`--help` CLI and `python -m` entry point,
  `conftest.py` (registers the `PENGYOS_IT` marker and wires the sibling
  `pengyos-kernel-fragments` library into `sys.path` when present), and
  `tests/test_scaffold.py` / `tests/test_github_ready.py`. `pytest -q` = 13
  passed. Runtime is standard-library only.
