# pengyos-mutations

**Journaled, reversible system changes for PengyOS.**

Pengy mutates freely, but every change is snapshotted, attested, revertible, and
learned. `pengyos-mutations` is the CLI + pluggable backend that makes that
possible: it snapshots a set of tracked paths, runs a command, records the
result in an append-only attest log, and can later **revert** the tracked files
or **converge** the change into a declarative profile patch.

```
snapshot → apply → record → revert → converge
```

## Install

```bash
pip install -e .          # editable install; provides the `pengyos-mutations` script
# or run without installing:
python -m pengyos_mutations --help
```

The project depends only on the Python standard library. The optional `test`
extra pulls in `pytest` for running the suite.

## Usage

```bash
pengyos-mutations apply --desc "add /etc/foo" --root /etc -- <cmd...>
pengyos-mutations revert <id>
pengyos-mutations log [--json]
pengyos-mutations converge <id> --out profile.patch.toml
```

`apply` snapshots the tracked `--root` (default: `$PENGYOS_MUTATIONS_ROOT` or the
current directory), runs `-- cmd...` under that snapshot, and records the result.
The command runs with its working directory at `--root` (override with `--cwd`);
pick a snapshot backend with `--backend` (default: `mock`). The process exits
with the command's exit code (a failed mutation is still recorded and remains
revertible). The `--store` directory defaults to `$PENGYOS_MUTATIONS_STORE` or
`~/.config/pengyos/mutations` and must live outside `--root`.

| Command   | What it does                                                        |
|-----------|---------------------------------------------------------------------|
| `apply`   | Snapshot → run command → record (journaled, reversible).            |
| `revert`  | Restore the tracked files for a recorded change.                    |
| `log`     | Print the attest log (optionally as JSON).                          |
| `converge`| Turn a recorded change into a declarative profile patch (best-effort). |

## Architecture

The CLI (`pengyos_mutations/cli.py`) is a thin dispatch layer. Feature modules
are imported lazily so the parser can be built and unit-tested before the
features exist:

- `backends.py` — the `Backend` interface (`snapshot`/`restore`) with a `mock`
  (copy-dir) backend and a documented btrfs stub.
- `apply.py` — snapshot → run command → record (via `pengyos-attest` if
  importable, else a local hash-chained chain) → capture the diff of tracked paths.
- `revert.py` — restore tracked files from a recorded snapshot.
- `converge.py` — emit a declarative profile patch from a captured change.
- `attest.py` — the local append-only, hash-chained event log.

## Testing

```bash
pytest -q
```

Tests never need the network, root, a real display, or real hardware — they use
fixtures and mocks. System integration tests are opt-in behind
`PENGYOS_IT=1`.

## License

MIT — see [LICENSE](./LICENSE). Copyright (c) 2026 Pat Wendorf.
