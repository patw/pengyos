# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **GitHub-ready contract now guards "docs in sync"** (`tests/test_repo.py::test_github_ready`).
  The unified GitHub-ready assertion (spec.md Verification) previously only
  checked that `README.md` / `LICENSE` / `CHANGELOG.md` / `.gitignore` exist with
  *some* content. It now also asserts the `README.md` documents every subcommand
  the CLI actually exposes (`version`, `validate`, `emit`, `lock`, `drift`) — the
  real reading of spec.md's "Docs are authored — keep them in sync". The
  subcommands are read live from the argparse parser (`build_parser()`), so a new
  undocumented subcommand fails the GitHub-ready contract (not just a later
  review); a monkeypatch negative-check confirms the guard catches an
  undocumented subcommand.
- `pengyos_pins.lockfile` — the resolved-component lockfile and `drift` layer.
  - **Lockfile** (`pins.lock.toml`) — a committed snapshot of the resolved
    components (version + sha256, plus the full component record) that `drift`
    compares the working `pins.toml` against. `serialize_pins()` (in
    `pengyos_pins.schema`) serialises a `Pins` to a deterministic, name-sorted
    `pins.lock.toml` document via a tiny stdlib-only TOML writer (`tomllib` can
    only *read* TOML — `tomli_w` is not a dependency); it round-trips losslessly
    through `tomllib.loads` + `parse_pins`, escaping `"`, `\` and control chars
    while passing non-ASCII through. `load_lockfile()` reads one back with the
    same schema loader (a malformed lockfile raises `tomllib.TOMLDecodeError` —
    a hard error, never a silent drift); `write_lockfile()` writes a validated
    manifest to disk.
  - **Drift** (`drift()`) — diffs a working `Pins` against its locked `Pins` and
    reports **added** (in pins, not the lock), **removed** (in the lock, not
    pins), **version-changed**, and **hash-changed** entries (spec.md: "drift
    detects added/removed/version-changed/hash-changed entries"). A component
    that changed both version and hash is reported once as a version change;
    each component appears at most once. Output is deterministically ordered
    (added → removed → version → hash, each by name). `format_drift()` renders a
    counts header plus one line per entry; `drift_counts()` gives the per-category
    counts.
- `pengyos-pins lock [PINS.TOML] [--lock PATH]` — writes a resolved lockfile
  (default `pins.lock.toml`) from a validated manifest. Only a manifest that
  passes validation is locked (the lockfile is the resolved baseline, so it must
  itself be valid). Exit codes: `0` success, `1` the file failed validation /
  could not be read / could not be parsed, `2` a usage error. Defaults to
  `pins.toml` / `pins.lock.toml`.
- `pengyos-pins drift [PINS.TOML] [--lock PATH]` — diffs the working manifest
  (default `pins.toml`) against its lockfile (default `pins.lock.toml`), printing
  a per-category count and one line per entry. Exit codes: `0` no drift, `1`
  drift detected (or the pins/lock file could not be read/parsed) — so it doubles
  as a CI guard against unreviewed manifest changes. A missing lockfile is a hard
  error (run `lock` first).
- `tests/test_lockfile.py` — serialisation (parseable TOML, name-sorted,
  deterministic, required fields, edition-conditional, escaping + non-ASCII
  round-trips), load/write round-trips (a written lockfile passes the whole
  validate contract), and drift (all four categories, version-takes-precedence
  over hash, deterministic ordering, empty input), plus `format_drift` /
  `drift_counts`.
- `pengyos_pins.schema.serialize_pins` (+ `LOCKFILE_FIELDS`, `_toml_escape`,
  `_toml_string`) — the stdlib-only TOML writer backing the lockfile.
- `pengyos-pins emit ...` — the emit layer for `pins.toml`. Turns a validated
  `Pins` manifest into Buildroot and report artefacts:
  - **Buildroot fragment** (`buildroot_fragment`) — one `CONFIG_<SYMBOL>=y` line
    per pinned component, in name-sorted order, with a Buildroot-safe symbol
    mapping (upper-case, dashes → underscores, so `linux-libre` →
    `CONFIG_LINUX_LIBRE`). Preceded by a short header comment; the output is
    deterministic and re-parseable via `parse_buildroot_fragment`, which recovers
    the emitted symbols (comment/blank lines ignored), so an emit → parse
    round-trip is lossless.
  - **Markdown report** (`emit_report`) — component count, editions, and a
    per-component table (name, upstream, version, license, edition, sha256).
  - **JSON report** (`emit_json`) — a pretty-printed, key-sorted document that
    re-parses to the same data (`component_count`, `editions`, `components`).
  - `emit(pins, fmt)` dispatches on `fmt` (`buildroot-fragment` | `report` |
    `json`); `ALL_FORMATS` is the single source of truth for the choices.
  - All emitters are **deterministic** (name-sorted, byte-identical per input)
    and **re-parseable** (spec.md: "emit is deterministic and re-parses").
  - **Deterministic emit ordering verified.** `emit` sorts by component name
    before rendering (every emitter — `buildroot_fragment`, `emit_report`,
    `emit_json` — plus `Pins.edition_names`), so the *input* order of a manifest
    never changes the bytes. `test_emit.py::TestDeterministicOrdering` proves
    this as **order-independence**: a `Pins` built from several scrambled input
    orders yields byte-identical output for every format, each emitter is
    confirmed name-sorted, and `edition_names` sorts + de-dups a larger mixed
    input. Version/hash in those fixtures are a pure function of the *name* (not
    position) so shuffling changes only order. A monkeypatch negative-check
    confirms the tests catch an ordering regression.
- `pengyos-pins emit [PINS.TOML] --format {buildroot-fragment,report,json}` —
  the `emit` subcommand. It first validates the manifest (emit only produces
  artefacts from a valid manifest), then prints the chosen format to stdout
  (default: `buildroot-fragment`). Exit codes: `0` success, `1` the file failed
  validation / could not be read / could not be parsed, `2` a usage error
  (e.g. an unknown `--format`). Defaults to `pins.toml`.
- `tests/test_emit.py` — 30+ cases: the Buildroot fragment (one line per
  component, name-sorted, symbol mapping, header, trailing newline, determinism,
  emit → parse round-trip, empty manifest), `parse_buildroot_fragment`
  (comments/blanks/arbitrary values/non-config lines), the Markdown report
  (count, editions, `(none assigned)`, table, sorting, determinism), the JSON
  report (valid, fields, round-trip, determinism), the `emit` dispatcher
  (all formats, unknown format raises), and the `emit` CLI (in-process and
  subprocess for every format, the default format, an invalid `--format` → exit
  2, and a missing file).
- `pengyos_pins.validate` — the semantic validation layer for `pins.toml`. The
  schema layer (`pengyos_pins.schema`) guarantees a component's *shape*
  (required fields, string typing, non-empty values, the `[a-z0-9][a-z0-9_-]*`
  name rule, uniqueness); this layer enforces the *semantic* rules the schema
  deliberately defers (see `AGENTS.md`, iteration 2):
  - **SPDX license syntax** — `validate_spdx()` runs a self-contained
    recursive-descent parser over the SPDX expression grammar
    (`AND` / `OR` / `WITH`, parentheses for grouping, and the trailing `+`
    or-later marker). It validates expression *well-formedness* without needing
    a copy of the (very long) SPDX identifier list, so it stays network-free.
  - **Hash format** — `validate_sha256()` requires a 64-character lowercase hex
    digest (`^[0-9a-f]{64}$`).
  - `validate_component()` / `validate_pins()` collect *every* problem and
    return a list of messages (empty == valid), each prefixed with
    `component[i]:` so a caller can report all failures at once.
- `pengyos-pins validate [PINS.TOML]` — the `validate` subcommand. It runs the
  whole contract: `load_pins` (required fields) then `validate_pins` (SPDX +
  hash). Exit codes: `0` valid, `1` the file failed validation or could not be
  read/parsed, `2` usage error. Prints `ok: N component(s) valid` on success,
  one `invalid: <message>` line per failure on stderr. Defaults to `pins.toml`.
- `tests/test_validate.py` — 40+ cases: every valid/invalid SPDX expression,
  every valid/invalid sha256 digest, the orchestrator, the committed
  `tests/fixtures/` (one failing fixture per rule — `bad_spdx.toml`,
  `bad_sha256.toml`, plus `valid.toml`), and the `validate` CLI (in-process and
  subprocess).
- `tests/fixtures/` — committed `valid.toml` (passes every rule) and one
  failing fixture per semantic rule, satisfying spec.md's "each validation
  rule has a failing fixture".
  `Component` dataclass (name, upstream, version, url, sha256, license, plus
  optional edition), the immutable `Pins` container, and the pure,
  side-effect-free `load_pins`/`parse_pins` loader. Enforces the schema at
  parse time: `component` array-of-tables structure, required-field presence,
  string typing, non-empty values, the `[a-z0-9][a-z0-9_-]*` name rule, name
  uniqueness, and rejection of unknown fields. Exposes `REQUIRED_FIELDS`,
  `OPTIONAL_FIELDS`, `COMPONENTS_KEY`, and a `component_table` test helper.
  `PinsError` is the single validation exception. `Pins.edition_names` returns
  the sorted, de-duplicated edition set in use.
- `tests/test_schema.py` — 50+ cases across the model, structural errors,
  field errors, value errors, lookups, disk loading, and a skip-guarded
  committed-manifest check.

## [0.1.0] - 2026-09-14

### Added
- Initial repo scaffold: `pyproject.toml` (setuptools backend, stdlib-only
  runtime, `pengyos-pins` console script → `pengyos_pins.cli:main`),
  `pengyos_pins/` package with an argparse CLI (`--help`, `--version`,
  `version` subcommand) and `__main__`.
- pytest harness (no network, no root; integration tests opt-in via
  `PENGYOS_IT=1`).
- README, MIT LICENSE (Copyright (c) 2026 Pat Wendorf), CHANGELOG, .gitignore.
- `tests/test_repo.py::test_github_ready`: the unified GitHub-ready contract
  assertion (spec.md Verification). It verifies README.md (authored, links
  the license), LICENSE (MIT + the exact "Copyright (c) 2026 Pat Wendorf"
  line), CHANGELOG.md (authored with the 0.1.0 section), and .gitignore, then
  runs the full test suite in a fresh subprocess and asserts it is green. The
  sub-run deselects `test_github_ready` itself (via `-k`) to avoid infinite
  recursion; a non-zero return code (any failure, skip, or collection error)
  fails the check with a tail of the pytest output for debuggability. The
  pre-existing per-file repo checks remain as focused unit tests.
- `tests/test_cli.py`: module-version lockstep (pyproject `version` ==
  `pengyos_pins.__version__`, console script declared), parser behaviour
  (`--help`/`--version`/no-command exit codes, `version` subcommand), and
  real-subprocess CLI tests for `version` and the global `--version` flag.
