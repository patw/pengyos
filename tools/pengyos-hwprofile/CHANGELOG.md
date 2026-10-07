# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Conventional Commits](https://www.conventionalcommits.org/).

## [Unreleased]

### Added

- The **GitHub-ready contract** assertion (`tests/test_github_ready.py`), closing
  the `test_github_ready` subtask in `implementation.md`. It asserts, from
  committed repo files only (no network/root/hardware), that the project stays
  GitHub-ready: `README.md` and `CHANGELOG.md` are authored, `LICENSE` is MIT
  with "Copyright (c) 2026 Pat Wendorf", `.gitignore` ignores the generated
  `profile.toml`, `pyproject.toml` is valid and carries the
  `pengyos-hwprofile` console entry point, and `pyproject.toml`'s version is in
  sync with `pengyos_hwprofile.__version__`. `pytest -q` = 138 passed.

### Changed

- Formally closed the "Emit deterministic TOML; implement diff" subtask
  (`implementation.md`). The emitter (`pengyos_hwprofile.emitter`) and the
  `diff` subcommand were implemented together with the CLI in 0.4.0; this run
  verified them end-to-end and flipped the item to done.
- Added `tests/test_emit_diff_pipeline.py`: two `detect` runs over the same
  fixture are byte-identical (determinism through the whole collect→emit path),
  and a modified fixture surfaces an added device and a changed value through
  the real `detect`/`emit`/`diff` path.

## [0.4.0] — 2026-09-14

### Added

- The `pengyos-hwprofile` **CLI** (`pengyos_hwprofile.cli`), with two
  subcommands:
  - `detect [--from DIR] [--out PATH]` — collect a hardware profile from a
    *source* and emit it as deterministic `profile.toml`. With `--from DIR` the
    source is a directory (a committed fixture, or a real `/sys`+`/proc`
    mirror); with no `--from` the **live system** is read (the integration
    path, opt-in behind `PENGYOS_IT=1`). Output goes to `--out` or stdout.
  - `diff A B` — compare two profiles and report added (`+`), removed (`-`),
    and changed (`~`) devices in the hardware list sections.
- A deterministic **TOML emitter** (`pengyos_hwprofile.emitter`): renders a
  validated profile document to `profile.toml` with fixed key ordering, sorted
  list sections, and a byte-stable round-trip through `tomllib`. Standard
  library only (`tomllib` is read-only, so this is a small purpose-built
  writer).
- A **live source** (`pengyos_hwprofile.source.LiveSource` / `live_source()`):
  maps the collectors' relative paths (`proc/…`, `class/…`, `bus/…`) onto the
  real `/proc` + `/sys` roots, so the very same collectors read a live machine
  through the same code path as a fixture. `/proc` and `/sys` are separate
  mounts, so the live source bridges them without a sandbox escape.
- Tests for the emitter (`test_emitter.py`), the CLI (`test_cli.py`), the live
  source (`test_source.py`), and opt-in live integration (`test_integration.py`,
  behind `PENGYOS_IT=1`). Version bumped 0.3.0 → 0.4.0. `pytest -q` = 127
  passed (plus 3 live integration tests under `PENGYOS_IT=1`).

### Notes

- The emitter is a prerequisite of the `detect` subcommand (detect emits via
  it), so it lands together with the CLI here even though it is listed under
  the next subtask.
- The live path reads `/proc` + `/sys` directly (the same paths the collectors
  already read from a fixture); it does not shell out to `lspci`/`lsusb`/
  `dmidecode`. Enriching the collectors with those tool outputs is a possible
  follow-up.

## [0.3.0] — 2026-09-14

### Added

- The hwprofile **collectors** (`pengyos_hwprofile.collectors`): read a machine's
  hardware from a *source directory* and emit plain `dict`/`list` structures
  shaped exactly like the schema, ready for the TOML emitter.
  - `collect_metadata` — hostname / kernel / tool version.
  - `collect_cpu` — vendor / model / cores / threads / mhz from `cpuinfo`.
  - `collect_memory` — memory modules from `dmi` (locator / size / speed /
    manufacturer / type).
  - `collect_gpu` — display controllers from `pci` (vendor / model / bus /
    driver).
  - `collect_storage` — NVMe / SATA devices from `nvme` / `block`
    (bus / model / size / serial / transport).
  - `collect_network` — interfaces from `net` (interface / mac / driver /
    speed / bus).
  - `collect_peripherals` — USB devices from `usb`
    (bus / vendor_id / product_id / name).
  - `collect_all` / `collect` / `collect_profile` — assemble every section and
    validate against the schema (raising `ProfileError` on any violation).
- A new `source.py` module: `Source` (a read-only, path-safe view over a
  directory) and `open_source()`, so collectors read fixtures in tests and
  `/sys` on a real machine through the same code path.
- A new `parsers.py` module: `parse_cpuinfo` and `parse_pci_class` (maps a PCI
  class code to `display` / `storage` / `network`), the two non-trivial text
  parsers the collectors depend on.
- A committed fixture tree under `tests/fixtures/sys` (a realistic, hand-authored
  mirror of the parts of `/sys` + `/proc` the collectors read) plus an
  `empty` fixture for error-path tests.
- Tests for the source abstraction, the parsers, and every collector
  (`test_source.py`, `test_parsers.py`, `test_collectors.py`).

### Notes

- Collectors are pure over `Source`: no live filesystem access, no subprocess,
  no network — the whole suite runs with no root and no real hardware. Real
  system integration (pointing a `Source` at live `/sys` and running
  `lspci`/`lsusb`/`dmidecode`) is the next subtask.
- The `detect`/`diff` subcommands are still unimplemented (see
  `implementation.md`).

## [0.2.0] — 2026-09-14

### Added

- The hwprofile **profile schema** (`pengyos_hwprofile.schema`): the concrete
  data model (`Metadata`, `Cpu`, `MemoryModule`, `Gpu`, `StorageDevice`,
  `NetworkDevice`, `Peripheral`, and the top-level `Profile`) plus a strict
  `validate()` / `load_profile()` that enforces it.
- The schema defines the top-level document as a `metadata` table, a `cpu`
  table, and the optional list sections `memory`, `gpu`, `storage`, `network`,
  and `peripherals`. Violations raise `ProfileError` with a path-qualified
  message (e.g. `cpu.cores: expected int, got bool`). Unknown top-level
  sections and unknown table fields are rejected.
- Tests for the schema (valid documents, every violation class, frozen-model
  construction, and declarative-schema introspection).

### Notes

- The schema is the shared contract the later subtasks build on: collectors
  emit structures shaped like it, the TOML emitter serialises a validated
  `Profile`, and `diff` loads and validates two profiles before comparing.
- The `detect` and `diff` subcommands are still unimplemented (see
  `implementation.md`).

## [0.1.0] — 2026-09-14

### Added

- Repository scaffold for `pengyos-hwprofile`: stdlib-only Python package
  (`pengyos_hwprofile`), `pyproject.toml` with a `pengyos-hwprofile` console
  entry point, `README.md`, `LICENSE` (MIT), `CHANGELOG.md`, and `.gitignore`.
- Working `--version` / `--help` CLI surface (`pengyos_hwprofile.cli`) and a
  `python -m pengyos_hwprofile` entry point.
- Smoke tests for the package skeleton and CLI entry points.

### Notes

- The `detect` and `diff` subcommands are implemented in later subtasks (see
  `implementation.md`).
