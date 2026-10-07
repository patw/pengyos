# pengyos-btrfs-layout

Plan a **btrfs subvolume layout** for PengyOS (root, home, var, snapshots, …)
and generate the `btrfs subvolume` / snapshot commands needed to create it,
take a named snapshot, and roll back to a prior one.

## Why btrfs subvolumes

PengyOS treats the image as the base and the surface as the mutable,
agent-editable layer (see the project `SPEC.md`, §1 and §1.5). **btrfs
snapshots are the safety net for free agent mutation:** Pengy snapshots before
every change, and a broken change is rolled back — the machine always comes
back. This tool is the declarative planner for that safety net.

It plans the subvolume tree and emits the exact `btrfs` command lines a
provisioner would run. The tool **plans and generates commands; it never
touches a live filesystem in the test suite.** Every `btrfs` invocation goes
through a single mockable runner seam, so the argv is golden-testable without
root or a real btrfs volume.

## Install

Standard library only — no third-party runtime dependencies (Python ≥ 3.11,
for the stdlib `tomllib` reader used by the `layout.toml` round-trip).

```sh
pip install .          # installs the `pengyos-btrfs` command
# or run in place:
python -m pengyos_btrfs
```

## Usage

```
pengyos-btrfs plan --out layout.toml
pengyos-btrfs snapshot <name> [--json] [--dry-run]
pengyos-btrfs rollback <name> [--yes] [--dry-run]
```

- **plan** — plan the canonical subvolume layout and write it to a file
  (`--out layout.toml`); the document round-trips through `tomllib`
  (see `spec.md`, "Layout serialization (layout.toml)").
- **snapshot <name>** — create a named read-only snapshot: it runs the
  generated plan (every `btrfs` call through the mockable runner seam) and
  prints a human-readable step list. `--json` emits the plan (`Plan.to_dict()`)
  as JSON on stdout and executes nothing (generation mode); `--dry-run`
  previews the commands without running any of them.
- **rollback <name>** — roll back to a named snapshot. Rollback is destructive
  and **safe by default: it refuses to run without an explicit `--yes`
  confirmation flag** (the gate lives in `pengyos_btrfs.confirm` and fires
  before any `btrfs` call); on refusal the plan is still shown so you see
  exactly what `--yes` would do. `--dry-run` previews the rollback plan
  without running it and never needs `--yes`. See `spec.md`, "CLI handlers".

### Canonical layout

The planner's canonical PengyOS tree (flat: every subvolume is a sibling
directly under top-level) lives in `pengyos_btrfs.layout`:

| name         | path          | ro  | mount   | role                              |
|--------------|---------------|-----|---------|-----------------------------------|
| `@`          | `@`           | no  | `/`     | OS root — the frozen base image   |
| `@home`      | `/@home`      | no  | `/home` | user homes (mutable agent layer)  |
| `@var`       | `/@var`       | no  | `/var`  | logs/state (mutable)              |
| `@snapshots` | `/@snapshots` | yes | *(none)*| parent dir for named snapshots    |

`@snapshots` is not a mount point: it is a directory inside the root
subvolume hosting read-only named snapshots, so a snapshot can never shadow
a live mount point.

The command-line contract for every subcommand is locked in by the test
suite; see `spec.md` (`Interface`).

## Verification (tests are first-class)

- golden argv for plan/snapshot/rollback (no real `btrfs` needed — the runner
  is mockable);
- rollback requires `--yes` (the gate fires before any runner call);
- a `--dry-run` preview never calls the runner (even for an unconfirmed
  rollback);
- layout validation errors;
- the GitHub-ready contract (`tests/test_github_ready.py`).

```sh
python -m pytest -q          # unit tests (no network, no root)
```

Integration tests are opt-in behind `PENGYOS_IT=1` and are skipped otherwise.

`tests/test_github_ready.py` asserts the GitHub-ready contract from `spec.md`
(README / MIT LICENSE / CHANGELOG / `.gitignore` present and correct, docs in
sync with `spec.md`'s Interface, and the full test suite green — the latter
done by re-running pytest in a subprocess, excluding itself).

## Development

```sh
python -m pytest -q
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Pat Wendorf.
