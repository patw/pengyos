# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-09-15

### Added

- `converge` — turn a recorded change into a declarative profile patch
  (`pengyos_mutations/converge.py`):
  - `converge(change_id, out, store)` looks the apply record up in the attest
    log and emits a TOML profile patch (`[[file]]` entries) that reproduces the
    change's *effect* without re-running the command.
  - Only **file-content changes** (a file the command *created* or *modified*)
    are representable declaratively, as a `[[file]]` table with `path`, decoded
    `contents`, and octal `mode`. The result is a `ConvergeResult` with
    `entries` (the declared files) and `skipped` (`(path, reason)` pairs).
  - Best effort, by construction: **deleted** files are skipped (a declarative
    profile expresses desired state, not removals), **binary** content is
    skipped (not valid UTF-8), and files that **no longer exist** are skipped.
  - The patch is emitted with a tiny standard-library TOML writer (Python ships
    a TOML *reader*, `tomllib`, but no *writer*), keeping the package
    zero-dependency; the emitted document re-parses under `tomllib`.
  - `converge` CLI subcommand now writes the patch to `--out`, prints a
    one-line summary, returns `0` on success and `1` on failure
    (`ConvergeError`: unrecorded change, rootless record).

## [0.4.0] - 2026-09-15

### Added

- `revert` — restore the tracked files for a recorded change
  (`pengyos_mutations/revert.py`):
  - `revert_change(change_id, store)` looks the change up in the attest log,
    reads back the `backend` and tracked `root` from the apply record, and
    restores the tracked tree to its pre-change state via the backend's
    `restore`.
  - A change that *created* files has them removed; a change that *modified* or
    *deleted* files has them restored. What the revert undid is reported in a
    `RevertResult` (`restored` / `removed` lists) by diffing the tracked tree
    before and after the restore, so the report reflects reality rather than a
    possibly-stale recorded diff.
  - The reversal is appended to the attest log as a `revert` event, so the audit
    trail records both the mutation and its reversal and the chain still
    verifies end to end.
  - `find_change(store, change_id)` returns the matching apply record (or
    `None`); `RevertError` is raised for an unrecorded change, a rootless
    record, or a backend that cannot be loaded.
- `revert` CLI subcommand now returns `0` on success (printing a one-line
  summary) and `1` on failure (printing the error to stderr).

## [0.3.0] - 2026-09-15

### Added

- `apply` — the heart of the mutation loop (`pengyos_mutations/apply.py`):
  - `apply_change(desc, store, command, *, root, cwd, backend)` snapshots the
    tracked tree (via a `Backend`), runs the command under that snapshot, records
    the attempt in the attest log, and captures the tracked-path diff
    (created / modified / deleted).
  - The command runs with its working directory at the tracked root by default
    (override with `--cwd`); the tracked root defaults to `$PENGYOS_MUTATIONS_ROOT`
    or the current directory.
  - A failing command is still recorded (its exit code is captured) and the
    snapshot is preserved, so revert stays available.
  - `--backend` selects the snapshot backend (default `mock`); the store must
    live outside the tracked root (enforced).
- `attest` — a local append-only, hash-chained event log
  (`pengyos_mutations/attest.py`):
  - `append_event` (atomic temp-file + fsync + rename, `flock`-serialized),
    `verify_chain` (schema + self-hash + linkage + optional anchor/truncation),
    `read_records`, `last_record`, `chain_length`, `dump_log`.
  - Record schema and `sha256(prev_hash || canonical(record-without-hash))`
    hash rule are byte-for-byte compatible with the canonical `pengyos-attest`
    package; `apply` uses `pengyos-attest` when importable and falls back to
    this local chain otherwise.

### Changed

- `apply` CLI now returns the command's exit code (a failed mutation exits
  non-zero; an empty command exits 0).

## [0.2.0] - 2026-09-15

### Added

- Snapshot / restore backend layer (`pengyos_mutations/backends.py`):
  - `Backend` abstract interface (`snapshot` / `restore`).
  - `MockBackend` — a copy-dir backend (no privileges or special filesystems
    required) used for tests and local development.
  - `BtrfsBackend` — a documented stub; real btrfs snapshots require root and a
    btrfs filesystem, so it is intentionally not a runtime dependency.
  - `get_backend(name, store)` registry and a serializable `Snapshot` result.

## [0.1.0] - 2026-09-15

### Added

- Scaffold repo: `pyproject.toml`, the `pengyos_mutations` package, an argparse
  CLI with `apply` / `revert` / `log` / `converge` subcommands, GitHub-ready
  docs (README, LICENSE, CHANGELOG, .gitignore), and a green test suite.
