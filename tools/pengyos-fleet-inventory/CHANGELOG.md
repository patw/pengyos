# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Conventional Commits](https://www.conventionalcommits.org/).

## [Unreleased]

### Added

- The **`test_github_ready`** contract tests, closing the `test_github_ready`
  subtask in `implementation.md`.
  - `tests/test_github_ready.py` locks the spec's *GitHub-ready contract*: the
    repo ships `README.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat Wendorf"),
    `CHANGELOG.md`, `.gitignore` (keeps `profiles/` and `*.profile.toml` out of
    version control), and `pyproject.toml` with the `pengyos-fleet` console
    entry point and stdlib-only runtime. The README is checked to document every
    subcommand the CLI advertises, and the CHANGELOG is checked to record the
    most recently closed subtask — so the docs cannot silently drift from the
    code. The suite itself is asserted to be full (test modules present). No
    network, no root, no real hardware.
  - `pytest -q` = 153 passed (142 + 11 new), 3 skipped (integration, `PENGYOS_IT`).

- The **`diff`** subcommand, closing the `pengyos-fleet diff: compare two
  profiles (added / removed / changed devices)` subtask in `implementation.md`.
  - `pengyos_fleet.diff`: `load_profile(path)` loads and validates a
    `profile.toml` (reusing the sibling `pengyos_hwprofile` validator when
    importable, else a documented local copy — same typed `DiffError` either
    way); `diff_documents(before, after)` returns a stable list of
    `(kind, section, key)` tuples (`"+"` added, `"-"` removed, `"~"` changed);
    `render_diff(...)` formats them as `+ {section}: {key}` / `- ...` / `~ ...`
    lines (or a single "identical" line). `DiffError` covers a missing file,
    invalid TOML, and a schema violation.
  - The diff compares only the hardware *list* sections (`memory`, `gpu`,
    `storage`, `network`, `peripherals`); `metadata` / `cpu` are machine/tool
    identity and are not compared. Each section is keyed by its physical
    identity fields, so a value change is reported as `~ changed` and a device
    that appears only on one side is `+ added` / `- removed`. For `network` the
    interface *name* is the identity and the MAC is a property (a MAC change is
    a `~ changed`, not a removal plus a fresh addition) — this is a deliberate
    divergence from the sibling `pengyos_hwprofile` diff, which keys `network`
    by `(interface, mac)`, because a hardware inventory should not conflate a
    config change with a hardware swap.
  - The `diff` CLI subcommand
    (`pengyos-fleet diff A B`) prints the report to stdout and exits `0` when
    the profiles are identical, `1` when they differ, and `2` for bad input
    (missing file / invalid TOML / schema violation); errors go to stderr.
  - `pytest -q` = 142 passed (was 113), 3 skipped (integration, `PENGYOS_IT`).

- The **collect-only-for-changed** (idempotent) re-collect, closing the
  `Implement collect-only-for-changed (idempotent)` subtask in `implementation.md`.
  - `pengyos_fleet.emit` gains `emit_profile_if_changed(runner, host, out_dir)`,
    which renders a fresh profile and compares it byte-for-byte against the
    `profile.toml` already on disk: when they match the host is left untouched
    and `None` is returned, otherwise the profile is (re)written and the path
    returned. `collect_all(runner, hosts, out_dir)` now walks the fleet through
    that primitive and returns only the paths actually written (in host order) —
    an all-unchanged re-collect writes nothing and returns `[]`.
  - The `collect` CLI subcommand now reports each host's outcome — `wrote
    <path>` for a changed/new profile, `unchanged <path>` for a host left
    untouched — so an idempotent re-collect is visible rather than silent.
  - The writer being deterministic is what makes this correct: a re-run
    reproduces byte-identical bytes, so a stale profile (one that drifted from a
    fresh collect) is the only thing rewritten. `pytest -q` = 113 passed (was
    103), 2 skipped (integration, `PENGYOS_IT`).

- The **emit** step, closing the
  `Emit one profile per host (reuse the pengyos-hwprofile schema if importable,
  else a documented local copy)` subtask in `implementation.md`.
  - `pengyos_fleet.emit`: `render(document)` serialises a collected document to
    deterministic, diff-friendly `profile.toml` text, and `emit_profile(runner,
    host, out_dir)` / `collect_all(runner, hosts, out_dir)` write one
    `<out>/<name>/profile.toml` per host (creating the output dir as needed).
    The `collect` CLI subcommand wires it together:
    `pengyos-fleet collect --hosts hosts.toml --out profiles/ [--runner ssh|mock]`
    (default transport `ssh`; tests pass `--runner mock`).
  - The writer reuses the sibling `pengyos_hwprofile.emitter` **when it is
    importable** (the test suite adds the sibling to `sys.path`; see
    `tests/conftest.py`) and otherwise falls back to a documented,
    standard-library-only local copy — **byte-identical** either way. The local
    copy is kept in lock-step with the hwprofile writer: stdlib ships
    `tomllib` (read-only) but no writer, so the hwprofile emitter is a
    hand-rolled TOML writer for exactly the subset the schema needs.
  - Output is deterministic: `[metadata]` / `[cpu]` in a fixed key order, list
    sections in a fixed order, list items sorted by a stable identity key, and a
    single trailing newline. Every emitted profile round-trips through
    `tomllib` and validates against the hwprofile schema.
  - `pytest -q` = 103 passed (was 70), 2 skipped (integration, `PENGYOS_IT`).

- The **fact collection** layer, closing the
  `Implement fact collection (cpu, mem, gpu, storage, nic) parsing command
  output fixtures` subtask in `implementation.md`.
  - `pengyos_fleet.facts`: `collect_facts(runner, host)` runs a fixed set of
    hardware commands through the injectable runner and parses each into the
    matching hwprofile section. The command registry (`commands()`) is the
    single source of truth the CLI and tests share.
  - One pure parser per command, all standard library only: `uname -a` →
    `metadata` (`hostname`/`kernel`), `lscpu` → `cpu` (vendor/model/cores/
    threads/mhz, cores = sockets × cores/socket), `free -b` → `memory` (total
    RAM as a single module, since per-DIMM detail needs root), `lspci` → `gpu`
    (display controllers, named via an embedded PCI vendor/device table),
    `lsblk -P` → `storage` (bus/model/size/serial/transport), and
    `ip -o link show` → `network` (interface/MAC). Non-zero command exit raises
    a typed `FactsError` so a missing hardware tool fails loudly.
  - Collected documents validate against the sibling `pengyos_hwprofile` schema
    (the schema is imported when importable; the collection code stays
    importable without it). Committed fixtures for `uname`/`lscpu`/`free`/
    `lspci`/`lsblk`/`ip` under `tests/fixtures/hosts/{n1,n2}/`.
  - `pytest -q` = 70 passed (was 38).

- The **runner interface** and **host-list schema**, closing the
  `Define the runner interface (ssh / mock) + hosts.toml schema` subtask in
  `implementation.md`.
  - `pengyos_fleet.hosts`: the `Host` dataclass and a strict `load_hosts()`
    TOML loader. The `hosts.toml` schema is a top-level `[[hosts]]` array of
    tables — `name` (required, also the output dir), `address` (required, SSH
    target), and an optional `fixtures` path (resolved relative to the file,
    consumed by the mock runner). Missing/invalid input raises a typed
    `HostsError`.
  - `pengyos_fleet.runner`: the injectable runner seam behind `make_runner()`.
    `SSHRunner` shells out to `ssh` (`ssh [opts] -- [user@]host command...`,
    `--` guarding a remote command that begins with a dash); `MockRunner`
    returns committed fixture output, one file per command named after the
    command's basename, so the whole flow runs offline. Both raise a typed
    `RunnerError`.
  - Committed mock fixtures under `tests/fixtures/hosts/{n1,n2}/` and a
    `tests/fixtures/hosts.toml` sample list (plus `hosts.example.toml` at the
    repo root). `pytest -q` = 38 passed.

- The **scaffold** for `pengyos-fleet`, closing the `scaffold repo` subtask in
  `implementation.md`. A stdlib-only Python package (`pengyos_fleet`) with a
  `pengyos-fleet` console entry point, a `python -m pengyos_fleet` entry point,
  `pyproject.toml` (packaging metadata + pytest config), `README.md`, `LICENSE`
  (MIT, "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, and `.gitignore`.
  Smoke tests for the package skeleton and CLI entry points. `pytest -q` = 9
  passed.

### Notes

- The `diff` subcommand now lands (see the `Added` entry above). The only
  `collect`/`diff` item still open is `test_github_ready`
  (`implementation.md`).

## [0.1.0] — 2026-09-18

### Added

- Repository scaffold for `pengyos-fleet`: stdlib-only Python package
  (`pengyos_fleet`), `pyproject.toml` with a `pengyos-fleet` console entry
  point, `README.md`, `LICENSE` (MIT), `CHANGELOG.md`, and `.gitignore`.
- Working `--version` / `--help` CLI surface (`pengyos_fleet.cli`) and a
  `python -m pengyos_fleet` entry point.
- Smoke tests for the package skeleton and CLI entry points.

### Notes

- The `collect` and `diff` subcommands are implemented in later subtasks (see
  `implementation.md`).
