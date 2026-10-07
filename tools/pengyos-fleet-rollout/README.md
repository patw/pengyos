# pengyos-fleet-rollout

**Plan, stage and roll back a PengyOS image/update rollout across a fleet.**

`pengyos-fleet-inventory` *describes* the fleet, but
nothing planned or reversed a rollout before this. Shipping one bad image to
every machine with no way back is the failure mode this closes.

`pengyos-fleet-rollout` turns a built PengyOS image (or a pending update set)
plus a fleet inventory into a **staged rollout plan** — deterministic,
canary-first batches, each with an explicit health gate, and a per-host
**rollback record** — then drives it through an **injectable transport**
(no-op / dry-run by default), and proves `rollback` restores the recorded prior
artifact.

## Install

```bash
pip install -e .            # editable dev install
pip install -e '.[dev]'     # + pytest
```

Runtime dependencies: **none** (standard library only).

## Usage

```bash
# 1. Plan a staged rollout (canary first) from a built image + the fleet.
pengyos-fleet-rollout plan --image img-2026.09.25 --inventory inventory.toml --json > plan.json

# 2. Apply it through an injectable transport. Dry-run by default (renders the
#    argv it would run); --commit actually runs it.
pengyos-fleet-rollout apply --plan plan.json --journal journal.json --dry-run
pengyos-fleet-rollout apply --plan plan.json --journal journal.json --commit

# 3. Roll back: restore every touched host's prior artifact, in reverse.
pengyos-fleet-rollout rollback --journal journal.json --commit
```

Or as a module:

```bash
python -m pengyos_fleet_rollout --version
python -m pengyos_fleet_rollout --help
```

### The three commands

* **`plan`** — read a fleet inventory plus a built image id and emit a staged
  plan: deterministic, canary-first batches (`--batch-size`), each with an
  explicit health gate, and a per-host rollback record. An empty inventory or an
  unknown image is a hard error.
* **`apply`** — drive the plan one batch at a time through an injectable
  transport, recording a journal entry before each host. The rollout halts on
  the first failing gate; the journal stays resumable. Dry-run by default.
* **`rollback`** — from a journal, re-apply each rolled host's prior artifact in
  reverse order. Idempotent: a host already back is a no-op.

### The transport

`apply` and `rollback` never touch a machine directly. They go through an
**injectable transport**:

* `--transport noop` — the default. Renders the argv it would run without
  invoking anything (the side-effect-free path the test suite asserts on).
* `--transport subprocess` — shells out to a command per host (production).

Real system integration (a real transport, a real fleet) is opt-in; the
default behaviour is fixture-driven and offline.

### The journal

`apply` writes a **journal** — declarative data recording, per host, the prior
artifact it had and the artifact it was rolled forward to, plus its state.
`rollback` reads that journal and restores each host's prior artifact. Because
the journal is data, rollback is reproducible and inspectable.

## The fleet inventory

`plan` consumes a `pengyos-fleet-inventory` fleet description — a `[[hosts]]`
array of tables (`name`, optional `role`/`priority`/`address`). See
`hosts.example.toml` for a ready-to-copy sample. `plan` rejects an empty
inventory or an unknown image.

## The data model

`plan`, `apply` and `rollback` all move data in and out of two committed,
schema-validated documents (standard library only — no third-party schema
engine; the model validates itself):

* **Plan** (`RolloutPlan`) — the output of `plan`: a built image id, the
  ordered, canary-first batches (each with an explicit health gate), and a
  per-host **rollback record** (`host`, `prior`, `new`). `prior` is a `UNKNOWN`
  placeholder until `apply` records the real prior artifact.
* **Journal** (`RolloutJournal`) — the output of `apply`: one entry per host
  (`host`, `prior`, `new`, `state`, UTC timestamp). `rollback` reads it and
  restores each host's prior artifact, in reverse.

Both serialise to and from JSON (`as_dict` / `from_dict` / `to_json` /
`load` / `dump`) and validate against a small hand-rolled schema on load — a
malformed plan or journal fails loudly. `tests/fixtures/` ships a committed
`inventory.toml`, the `plan.json` it produces, and a `journal.json` showing a
partial rollout with a mid-rollout failure.

## Tests

The suite is hermetic: no network, no root, no real display, no real hardware.
The transport is faked and the inventory is a committed fixture.

```bash
pytest -q
```

Integration tests (a real transport, a real fleet) are opt-in:

```bash
PENGYOS_IT=1 pytest -q
```

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Pat Wendorf.
