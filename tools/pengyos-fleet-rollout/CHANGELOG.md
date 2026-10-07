# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Conventional Commits](https://www.conventionalcommits.org/).

## [Unreleased]

### Added

- The **GitHub-ready contract tests** (`tests/test_github_ready.py`), closing the
  `test_github_ready` subtask in `implementation.md`. The spec's
  *GitHub-ready contract* asserts, with `test_github_ready`, that the repo ships
  everything a human needs to clone it and run it — `README.md`, `LICENSE`
  (MIT, "Copyright (c) 2026 Pat Wendorf"), `CHANGELOG.md`, `.gitignore`,
  `pyproject.toml`, and a full green test suite — with the docs kept in sync
  with the code. This run deepened the suite so the contract can't silently rot:
  - `test_gitignore_un_ignores_committed_fixtures` — the root-level
    `plan.json` / `journal.json` patterns also match `tests/fixtures/`, so the
    fixtures must be explicitly un-ignored (a fresh clone would otherwise ship
    with no fixtures); the un-ignore is asserted to come *after* the ignore.
    (The rollback subtask's gotcha — committed fixtures were silently
    git-ignored — now locked at the contract level.)
  - `test_readme_documents_the_cli_flags` — the README must document the actual
    CLI flags (`--image` / `--inventory` / `--batch-size` / `--json` /
    `--plan` / `--journal` / `--transport` / `--dry-run` / `--commit`), not just
    the subcommand names, so a visitor can run it.
  - `test_repo_is_a_git_repository` / `test_repo_tracks_its_source_files` — a
    GitHub-ready repo is a git work tree that actually *tracks* its package,
    suite, and committed fixtures (`git ls-files --error-unmatch`); when git is
    unavailable these skip rather than fail.
  - `test_hosts_example_toml_exists_and_parses` — the README points visitors at
    `hosts.example.toml` as a ready-to-copy sample inventory, so it must exist
    and parse as a valid `pengyos-fleet-inventory` (`load_inventory`), with a
    canary host.
  - `tests/test_github_ready.py` — 15 tests total (10 prior + 5 new),
    `pytest -q` = 194 passed (189 prior + 5 new), 0 failed; `PENGYOS_IT=1` also
    green.

- The **ground-up failure/recovery path** (`tests/test_failure_recovery.py`),
  closing the `Ground the failure/recovery path: a fake transport that fails on
  the 2nd host proves apply halts and rollback returns every touched host to
  its prior artifact (tree/state equality)` subtask in `implementation.md`.
  - A single cross-cutting integration test drives `apply` and `rollback` end to
    end against the same journal, with a fake transport that fails on the 2nd
    host (core-1): it proves `apply` halts on the first failing gate (canary-1
    applied, core-1 recorded :data:`~pengyos_fleet_rollout.model.STATE_FAILED`,
    the rest untouched) and `rollback` then returns *every touched host* to its
    prior artifact — the applied host is rolled back from its `new` to its
    `prior`, and the failed host is restored to the prior it never lost.
  - The fake transport reports the artifact it observed the host currently has
    **even on a failed step** (so `apply` records the real prior in the failed
    entry); for a rollback step that observed prior is the artifact the step
    already resolved to (an applied host's `new`, a failed host's `prior`).
  - `tests/test_failure_recovery.py` — 8 tests locking the halt, the failed
    host's recorded prior, reverse-order restore of every touched host, the
    applied host's `new`→`prior` transition, the failed host's no-op restore,
    the post-recovery journal state (all touched hosts rolled back, none
    applied), idempotency after full recovery, and the `apply`→`rollback` CLI
    path. `pytest -q` = 189 passed (181 prior + 8 new), 0 failed;
    `PENGYOS_IT=1` also green.

- The **rollback** behaviour (`pengyos_fleet_rollout.rollback`), closing the
  `Implement `rollback`: from a journal, restore each rolled host's prior artifact in reverse order; idempotent (a host already back is a no-op)` subtask in
  `implementation.md`.
  - `rollback(journal_path, *, transport="noop", commit=False)` loads the
    :class:`~pengyos_fleet_rollout.model.RolloutJournal` :func:`pengyos_fleet_rollout.apply.apply`
    wrote and walks every touched host in **reverse** rollout order — the last
    host rolled out is the first restored. A host whose latest entry is
    :data:`~pengyos_fleet_rollout.model.STATE_ROLLED_BACK` is already back; every
    other touched host (``applied`` **or** ``failed``) is rolled back: the
    transport is asked to move it from the artifact it currently has (the
    applied ``new``) back to the artifact it had before (the applied ``prior``),
    and a :data:`STATE_ROLLED_BACK` entry is appended recording the restore
    transition (``prior`` = the current artifact, ``new`` = the restored prior).
    A failed rollback gate records the failure and halts.
  - **Idempotent**: re-running rollback against a fully-reversed journal touches
    nothing (every touched host is reported as a no-op, in canonical order).
    Commit rewrites ``journal_path`` in place with the updated journal.
  - `tests/test_rollback.py` — 21 tests locking reverse-order restore, the
    step's prior/new swap, the per-host ``rolled_back`` entry (records the
    restore transition: prior = current artifact, new = restored prior), the
    restored prior reported by the transport, idempotency (already-back hosts
    are no-ops, partial re-runs skip them), rolling back a partial rollout that
    includes a ``failed`` host, the dry-run path (argv rendered, no transport
    call, nothing written), the on-disk rewrite, every hard error (missing /
    malformed journal, unknown transport, transport run error), the failing-gate
    halt, and the `rollback` CLI surface. `pytest -q` = 181 passed (160 prior +
    21 new), 0 failed; `PENGYOS_IT=1` also green.

- The **apply** behaviour (`pengyos_fleet_rollout.apply`), closing the
  `Implement `apply`: run batch-by-batch, record a journal entry before each host, stop the rollout on the first failing gate, and leave the journal resumable` subtask in
  `implementation.md`.
  - `apply(plan_path, *, transport="noop", journal_path=None, commit=False)`
    drives a :class:`~pengyos_fleet_rollout.model.RolloutPlan` through an
    injectable :class:`~pengyos_fleet_rollout.transport.Transport` one batch,
    then host, in order. It records a journal entry for each host as it is
    touched, and halts on the first failing gate (`ok=False`): the failed host
    is recorded :data:`~pengyos_fleet_rollout.model.STATE_FAILED` and the rest
    of its batch plus every later batch stay untouched.
  - **Resume**: `apply` loads any existing journal and skips hosts already
    :data:`~pengyos_fleet_rollout.model.STATE_APPLIED`; everything else is
    retried in order, so a halted rollout resumes where it left off. A host's
    prior artifact is resolved from the journal (an applied host's `new`, a
    failed/rolled-back host's `prior`) or the plan's rollback record.
  - The transport reports the prior artifact the host currently has via the new
    :attr:`~pengyos_fleet_rollout.transport.TransportResult.prior` field on
    `TransportResult` (the noop transport echoes the step's prior; a real
    transport reports what it observed).
  - `commit=False` (the default, `--dry-run`) renders each host's step without
    invoking the transport and writes nothing; `commit=True` (`--commit`) runs
    the transport and persists the journal to `journal_path`.
  - `tests/test_apply.py` — 30 tests locking the dry-run path (argv rendered, no
    transport call, nothing written), the committed path (per-host journal
    entries, prior reported by the transport, address forwarded from the
    inventory), halting on the first failing gate (failed host recorded, rest
    untouched), resumability (skip applied, retry failed, roll-forward from
    journal state), error handling (unknown transport, missing/malformed plan,
    transport run error), and the `apply` CLI surface. `pytest -q` = 160
    passed (128 prior + 32 new), 0 failed; `PENGYOS_IT=1` also green.

- The **transport** abstraction (`pengyos_fleet_rollout.transport`), closing the
  `Implement the transport abstraction: a subprocess-backed transport plus a `--dry-run` that renders the exact argv it would run (the side-effect-free path the suite asserts on)` subtask in
  `implementation.md`.
  - The transport is the injectable seam `apply` / `rollback` drive — they build a
    :class:`~pengyos_fleet_rollout.transport.TransportStep` per host and call
    :meth:`~pengyos_fleet_rollout.transport.Transport.run`, never touching a
    machine directly. The seam is a small runtime-checkable
    :class:`~pengyos_fleet_rollout.transport.Transport` protocol, so a noop /
    subprocess / fake all sit behind the same interface.
  - `NoopTransport` — the default, side-effect-free path. It renders the argv a
    subprocess transport would run and reports success without ever invoking
    anything (the `--dry-run` / `--transport noop` path the suite asserts on).
  - `SubprocessTransport` — subprocess-backed. Reads the command from
    `PENGYOS_ROLLOUT_TRANSPORT_CMD` (falling back to `pengyos-fleet-rollout-transport`),
    runs `build_argv` for the host, and maps a non-zero exit to a failed
    `TransportResult` (`ok=False`) — the mid-rollout gate failure a rollout halts
    on. A command that cannot run raises `RolloutTransportError`.
  - `TransportStep` (`action` / `host` / `prior` / `new` / `address`) and
    `TransportResult` (`ok` / `argv` / `returncode` / `output`) are frozen
    dataclasses; `build_argv` renders the deterministic argv
    `[command, action, host, prior, new]` with `--address <address>` appended
    when present; `make_transport` is the name-based factory the CLI dispatches
    on (`noop` / `subprocess`, unknown → `RolloutTransportError`).
  - `tests/test_transport.py` — 21 tests locking `build_argv` (exact argv,
    address inclusion/omission, custom command), the side-effect-free noop path,
    the subprocess path driven by a fake executable (success / non-zero-failure /
    env-command selection / missing-command error / stderr capture), the
    `make_transport` factory, and the `Transport` protocol. `pytest -q` = 128
    passed (107 prior + 21 new), 0 failed; `PENGYOS_IT=1` also green.

- The **plan** behaviour (`pengyos_fleet_rollout.plan`), closing the
  `Implement `plan`: read a fleet inventory (hosts + role/priority) and produce
  deterministic batches (canary first) each with an explicit health gate;
  rejected on an empty inventory or an unknown image` subtask in
  `implementation.md`.
  - `plan(image, inventory_path, batch_size=1)` reads the inventory and orders
    the fleet **canary-first**, then by priority (desc) then name (asc), groups
    it into deterministic batches of at most `batch_size`, and wraps that in a
    `RolloutPlan` with an explicit `all_healthy` health gate per batch and a
    per-host rollback record (`prior` is the `UNKNOWN` placeholder until `apply`
    records the real prior artifact). An empty, missing, or malformed inventory
    is a `RolloutPlanError` (the CLI maps it to exit 2), as is an unknown image.
  - The ordering and batching are pure, unit-tested helpers: `order_hosts`
    (canary-first, priority desc, name asc — a missing priority sorts last) and
    `group_into_batches` (stable chunking that keeps every host; batch size 0
    is rejected). `is_known_image` rejects an empty, blank, malformed, or
    digit-less id as an unknown image.
  - `RolloutPlan.render_text()` — a line-oriented summary (one line per batch
    with its hosts and gate, then the per-host rollback record) for the CLI's
    non-`--json` path.
  - `tests/test_plan.py` — 25 tests locking the ordering, the batching, the
    canary-first gate/rollback structure, the committed-fixture parity, and
    every hard error (unknown image, empty/missing/malformed inventory, bad
    batch size) plus the `plan` CLI surface. `pytest -q` = 107 passed
    (68 scaffold + model + github-ready, 39 new), 0 failed; `PENGYOS_IT=1` also
    green.

- The **scaffold** (pyproject, `pengyos_fleet_rollout/`, pytest,
  README/CHANGELOG/LICENSE/.gitignore), closing the
  `Scaffold repo (pyproject, pengyos_fleet_rollout/, pytest, README/CHANGELOG/LICENSE/.gitignore)`
  subtask in `implementation.md`.
  - `pyproject.toml` declares the `pengyos-fleet-rollout` package (stdlib-only
    at runtime), the `pengyos-fleet-rollout = pengyos_fleet_rollout.cli:main`
    console entry point, and a `[tool.pytest.ini_options]` block that puts the
    project root on `pythonpath` so the suite runs with no network, no root, and
    no real hardware.
  - The `pengyos_fleet_rollout` package: `__init__` (version + typed errors),
    `errors` (`RolloutError` base + `RolloutPlanError` / `RolloutTransportError`),
    `__main__` (`python -m pengyos_fleet_rollout`), and `cli` — a parser that
    advertises the spec's `plan` / `apply` / `rollback` subcommands with their
    documented arguments (plan: `--image`/`--inventory`/`--batch-size`/`--json`;
    apply/rollback: `--plan`/`--journal`/`--transport`/`--dry-run`/`--commit`,
    defaulting to a dry run). The handlers dispatch to their own modules, which
    the matching subtasks fill in.
  - `tests/conftest.py` registers the `PENGYOS_IT` marker and drops integration
    tests unless `PENGYOS_IT` is truthy. `tests/test_scaffold.py` asserts the
    package skeleton and the working `--version` / `--help` CLI surface;
    `tests/test_github_ready.py` locks the spec's *GitHub-ready contract*.
  - `README.md`, `CHANGELOG.md`, `LICENSE` (MIT, "Copyright (c) 2026 Pat
    Wendorf"), `.gitignore`, and `hosts.example.toml` (a ready-to-copy fleet
    inventory sample).
  - `pytest -q` = 21 passed (11 scaffold + 10 github-ready), 0 failed.
- The **data model** (`pengyos_fleet_rollout.model`), closing the
  `Model a rollout plan + a per-host journal entry (host, prior artifact, new
  artifact, state) as committed, schema-validated data` subtask in
  `implementation.md`. This subtask owns *what a rollout is* before any of it is
  produced or consumed, so `plan` / `apply` / `rollback` (later subtasks) all
  move data in and out of exactly these two shapes:
  - `RolloutPlan` — the output of `plan`: a built image id, the ordered,
    canary-first `Batch`es (each with a `HealthGate`), and a `RollbackEntry`
    per host (the per-host rollback record; `prior` is a `UNKNOWN` placeholder
    until `apply` fills it in).
  - `RolloutJournal` — the output of `apply`: a tuple of `JournalEntry`s
    (`host`, `prior`, `new`, `state`, UTC `at` timestamp). `rollback` reads it
    and restores each host's prior artifact; `applied_hosts` /
    `rolled_back_hosts` read the latest entry per host.
  - Both are plain `dict` documents that validate against a small, hand-rolled
    stdlib JSON-schema-style `validate()` (typed / enum / required / nested /
    array — `None` means optional, so optional fields round-trip cleanly) and
    round-trip through JSON (`as_dict` / `from_dict` / `to_json` / `load` /
    `dump`). The schema documents themselves are module constants, so a plan or
    journal is declarative data that can be committed and inspected.
  - `load_inventory` — a strict stdlib `tomllib` loader for the
    `pengyos-fleet-inventory` `[[hosts]]` schema (one `InventoryHost` per
    entry; a missing file, invalid TOML, a missing `hosts` array, or a
    malformed / field-less entry raises a typed `ModelError`).
  - Committed fixtures under `tests/fixtures/`: `inventory.toml` (a tiny
    canary-first fleet), `plan.json` (the plan that inventory produces with
    `--batch-size 1`), and `journal.json` (a partial rollout with a mid-rollout
    `failed` gate).
  - `tests/test_model.py` — 47 tests locking the schema, the JSON round-trip,
    the committed fixtures, and every malformed-input case. `pytest -q` = 68
    passed (21 scaffold + 47 new), 0 failed; `PENGYOS_IT=1` also green.
