# pengyos-backup

The **backup planner** for [PengyOS](../../README.md).

`pengyos-backup` implements USE-CASE **US-SRV-4**: a scheduled backup runs, a
documented restore to a clean target succeeds, and a backup failure alerts.
Recovery is **verified, not hoped for** — a restore-test harness proves a
documented restore reproduces a known tree.

Current version: 0.1.0.

## What it does

- **`plan`** — reads a `backup.yaml` and emits a systemd timer + oneshot
  service that runs restic/rustic with a golden argv (no guessing at flags).
- **`verify-restore`** — drives a restore from a fixture repository into a temp
  dir and asserts a tree hash. A corrupt/missing snapshot fails cleanly, so the
  restore proof has teeth.
- **`render`** — renders the parsed plan as JSON.
- **fail→alert wiring** — a backup failure invokes a documented alert hook.

## Configuration

The planner reads a `backup.yaml` describing **what** to back up, **where** to
store it, **when** to run, and **how many** snapshots to keep. The authoritative
schema lives in `pengyos_backup/config.py`; `example.backup.yaml` is a filled-in
template. Validation rejects missing/invalid documents with a message naming
the offending field (bad `version`, empty `sources`, a target missing its
`path`/`url`, an absent `schedule.on_calendar`, negative retention counts, a
retention policy that keeps zero snapshots, …).

```console
$ cp example.backup.yaml backup.yaml
```

```python
from pengyos_backup.config import load_config

plan = load_config("backup.yaml")  # raises ConfigError on a bad document
print(plan.sources, plan.schedule.on_calendar)
```

## Install

```console
$ pip install -e .
```

Requires Python ≥ 3.11. The only runtime dependency is PyYAML (the planner
reads `backup.yaml`).

## Usage

```console
$ pengyos-backup --help
usage: pengyos-backup [-h] [--version] <command> ...

Backup planner (restic/rustic) that emits a systemd timer + service and proves
restore with a tree-hash check (US-SRV-4).

<command>
  version        print the pengyos-backup version and exit
  plan           emit a systemd timer + service (restic/rustic golden argv)
  verify-restore restore from a fixture repo and assert a tree hash
  render         render the plan (parsed backup.yaml) as JSON
```

```console
$ pengyos-backup plan --config backup.yaml --out units/
$ pengyos-backup verify-restore --repo DIR --target tmp/ [--dry-run]
$ pengyos-backup render --config backup.yaml --json
```

`plan` writes four files into `--out`: `pengyos-backup.timer`,
`pengyos-backup.service`, `pengyos-backup-alert.service`, and the executable
`pengyos-backup-alert` hook. Install the `.timer`/`.service` into
`/etc/systemd/system/` (and the hook into `/usr/local/bin/`) and enable the
timer. `plan` is idempotent — re-running it yields the same bytes. The
`plan` command also accepts `--binary {restic,rustic}` to target either
backend (default `restic`).

## Development

Tests are the objective function. They need no network, root, display, or real
hardware — external tools are mocked and fixtures are committed. Integration
tests are opt-in behind `PENGYOS_IT=1`.

```console
$ pytest -q
```

See `spec.md` for the authoritative contract.

## License

MIT — see [LICENSE](LICENSE) (Copyright (c) 2026 Pat Wendorf).
