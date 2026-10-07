# PengyOS btrfs-layout — subvolume layout + snapshot/rollback

## Summary
Define the btrfs subvolume layout and generate snapshot/rollback commands.

## Purpose
SPEC §1.5: snapshots are the safety net for free agent mutation.

## Deliverable
A Python CLI `pengyos-btrfs` that plans a layout (subvolumes), generates `btrfs subvolume`/snapshot commands, and a rollback plan; btrfs calls are mockable.

## Layout (data + validation)

`pengyos_btrfs.layout` is the single source of truth for *what* the layout is
(discovered + pinned in the "Define the layout" task; the earlier spec left it
implied, which was too vague for two consistent implementations):

- **Canonical tree (flat):** every subvolume is a *sibling directly under the
  top-level* subvolume — no nesting of live subvolumes, so mount ordering
  stays trivial:

  | name           | path         | ro  | mount   | role                              |
  |----------------|--------------|-----|---------|-----------------------------------|
  | `@`            | `@`          | no  | `/`     | OS root — the frozen base image   |
  | `@home`        | `/@home`     | no  | `/home` | user homes (mutable agent layer)  |
  | `@var`         | `/@var`      | no  | `/var`  | logs/state (mutable)              |
  | `@snapshots`   | `/@snapshots`| yes | *(none)*| parent dir for named snapshots    |

  `@snapshots` is **not a mount point** (`mount=None`): it is a directory
  inside the root subvolume hosting read-only named snapshots, so a
  snapshot can never shadow a live mount point.
- **Data model:** frozen dataclasses `Subvolume(name, path, read_only,
  mount, description)` and `Layout(filesystem, subvolumes, snapshot_dir)`;
  `Layout.find(path)`, `Layout.snapshot_subvolume()`, `Layout.to_dict()`.
- **Validation (`validate_layout`, raises `LayoutError`, never a traceback):**
  filesystem is a non-empty name (not a path, ≤255 chars); subvolume names
  match `@([A-Za-z0-9._-]*)`; paths are `@` or `/@…` and the first path
  component must equal the subvolume's own name; mount targets are `/` or
  `/component…` with no `.`/`..` components and no trailing slash; names,
  paths and mounts are unique; non-root mount targets may not shadow each
  other (mounting under `/` is fine); nesting is rejected (flat tree only);
  exactly the required `@snapshots` subvolume must be present.

## Layout serialization (layout.toml)

`pengyos_btrfs.toml_io` serializes a validated `Layout` to the
`layout.toml` document the `plan --out` handler writes, and parses it back.
The stdlib can only *read* TOML (`tomllib`), so the emitter is a small
golden-tested writer covering exactly the subset `layout.toml` needs:
top-level `key = value` pairs (basic strings, booleans) plus a
`[[subvolumes]]` array of tables in layout (creation) order.

- **Format:** top-level `filesystem` and `snapshot_dir`; one
  `[[subvolumes]]` table per subvolume with `name`, `path`, `read_only`,
  `mount`, `description` (in that key order).
- **`mount` is omitted** for subvolumes that are not mounted
  (`mount is None`, e.g. `@snapshots`) — TOML has no null, and "no key" is
  the honest representation of "not a mount point"; the parser restores
  `None` for a missing `mount` (and `""` for a missing `description`).
- **Round-trip guarantee:** `layout_from_toml(text)` parses with `tomllib`
  (the authoritative reader) and re-validates through
  `validate_layout`, so `layout_from_toml(layout_to_toml(layout)) == layout`.
  Invalid TOML or a document that no longer describes a valid layout raises
  `LayoutError` — never a traceback.
- **CLI:** `plan --out FILE` writes the canonical layout's `layout.toml` to
  `FILE` and exits 0; an unwritable path is a clean `EXIT_ERROR` on stderr
  (no traceback).

## Command generation (data + argv)

`pengyos_btrfs.commands` turns a validated `Layout` into an immutable
`Plan` — an ordered sequence of `Step`s, each carrying a human `description`
and the exact `btrfs` `argv`. It is pure: nothing shells out. Real `btrfs`
invocations go through a mockable `BtrfsRunner` seam (`MockRunner` in tests,
`RealRunner` behind `PENGYOS_IT=1`), so the argv is golden-testable without
root or a real btrfs volume.

- **Staging mount point.** Every command addresses subvolumes by absolute path
  relative to a staging mount point (default `/mnt`, overridable per call): the
  filesystem is `mkfs`'d and mounted there, subvolumes are created under it, and
  the live mount points (`/`, `/home`, `/var`) are wired up afterwards. A
  layout-relative path `p` maps to `<mount>/<p>` (leading `/` collapsed).
- **create** (`create_commands(layout)`): one `btrfs subvolume create <mount><path>`
  per subvolume, in layout order. The top-level `@` is created explicitly —
  `mkfs.btrfs` only creates the anonymous default subvolume.
- **snapshot** (`snapshot_commands(layout, name)`): for every subvolume *except*
  the snapshot directory, one `btrfs subvolume snapshot -r <mount><path>
  <mount>/@snapshots/<name>/<name>`. Snapshot names are a single directory
  component (`[A-Za-z0-9._-]+`, not `.`/`..`, no `/`) — a bad name raises
  `SnapshotError`.
- **rollback** (`rollback_commands(layout, name)`): for each snapshotted
  subvolume, a *promote-then-swap* triplet. A snapshot lives under
  `@snapshots/<name>` — a different parent than the live subvolume — and
  `btrfs subvolume rename` only works within one parent, so the snapshot is
  first re-snapshotted (read-write) to a top-level sibling `<live>.rollback`,
  the live subvolume is deleted, and the sibling is renamed into place:
  `snapshot <mount>/@snapshots/<name>/<sv> <mount>/<sv>.rollback`, then
  `delete <mount>/<sv>`, then `rename <mount>/<sv>.rollback <mount>/<sv>`.
  Rollback is **destructive**; the safe-by-default gate that refuses to
  *execute* it without an explicit `--yes` lives in `pengyos_btrfs.confirm`
  (see "Confirmation and dry-run" below) and is consumed by the `rollback`
  CLI handler (see "CLI handlers" below).
- **Execution** (`execute_plan(runner, plan)`): runs every step through the
  runner in order, stopping at the first non-zero exit and raising
  `BtrfsCommandError` (with the failing `argv`/exit/output attached).

## Confirmation and dry-run (safe-by-default)

`pengyos_btrfs.confirm` is the safety net for the destructive path. It is pure
control flow over a `Plan` and the mockable `BtrfsRunner` (nothing shells out).

- **Destructive classification.** A plan *kind* is *destructive* when it is in
  `DESTRUCTIVE_KINDS` (currently `{"rollback"}`); `create` and `snapshot` are
  not. `is_destructive(kind)` / `requires_confirmation(kind)` report this.
- **The gate** (`require_confirmation(kind, *, confirmed, dry_run=False)`):
  raises `RollbackError` (never a traceback) when a **destructive** plan is
  about to be **executed** (not a dry-run) without `confirmed=True`
  (i.e. `--yes`). Non-destructive kinds are never gated; a dry-run and an
  explicitly confirmed plan both pass. The error message names both escape
  hatches: `--yes` to make the change, `--dry-run` to preview it.
- **Dry-run vs execute** (`execute_or_preview(runner, plan, *, confirmed,
  dry_run)`): in `--dry-run` mode the runner is **never called** and an
  `Execution(dry_run=True, results=())` is returned (so a rollback can be
  previewed without `--yes`). Otherwise the gate is enforced *first* (before any
  runner call), then the plan is executed via `execute_plan` (fail-fast),
  returning `Execution(dry_run=False, results=(RunResult, ...))` whose
  `argvs` property lists what actually ran.

## CLI handlers (plan / snapshot / rollback)

`pengyos_btrfs.cli` wires every subcommand to the pure pieces above. The
argument contract is the Interface below; the handlers add the semantics.

- **Runner seam.** Handlers execute plans through `cli.make_runner()`, a
  module-level factory returning a `RealRunner` by default. Tests substitute
  the factory with a `MockRunner` (monkeypatch), which is how the whole
  handler contract stays golden-testable without root or a real btrfs
  volume.
- **`plan --out FILE`** — serializes the canonical layout to `layout.toml`
  (see "Layout serialization") and writes it to `FILE`; exit 0 on success,
  clean `EXIT_ERROR` on an unwritable path.
- **`snapshot <name>`** — builds the plan via `snapshot_commands` (an
  invalid name is a clean `EXIT_ERROR` carrying the `SnapshotError`
  message on stderr, no traceback, runner untouched). Then, by precedence:
  - `--json`: emits `Plan.to_dict()` as JSON (`indent=2`, trailing newline)
    on stdout and exits 0 **without executing anything** — `--json` is the
    machine-readable *generation* mode, even combined with `--dry-run`.
  - `--dry-run`: previews via `execute_or_preview(..., dry_run=True)`
    (runner never called) and prints the human-readable step list with a
    "nothing will be executed" header and footer.
  - default: executes the plan (kind `snapshot` is non-destructive — never
    gated) and prints the human-readable step list plus a "N step(s)
    executed" summary.
- **`rollback <name>`** — builds the plan via `rollback_commands` (same
  invalid-name contract). No `--json` on `rollback` (it is destructive; an
  unknown flag is a usage error, exit 2). Then:
  - `--dry-run`: preview as above — no `--yes` needed; exits 0.
  - default (no `--yes`): the `require_confirmation` gate refuses **before
    any runner call**; the handler prints the plan (dry-run rendering) on
    stdout *and* the `RollbackError` message (naming `--yes` and `--dry-run`)
    on stderr, exiting `EXIT_ERROR`.
  - `--yes`: executes the plan; a non-zero step exit is a clean
    `EXIT_ERROR` (fail-fast at the failing step, never a traceback).
- **Human-readable step list** (`cli.format_plan`): a header line
  (`<prog>: <kind> plan (N step(s))`, suffixed "— dry-run, nothing will be
  executed" for previews), then per step an index line
  (`  [i/N] <description>`) and the exact argv line
  (`        $ <argv …>`). Pinned by golden tests in
  `tests/test_cli_handlers.py`.

## Interface
- pengyos-btrfs plan --out layout.toml
- pengyos-btrfs snapshot <name> [--json] [--dry-run]
- pengyos-btrfs rollback <name> [--yes] [--dry-run]

`--dry-run` previews a plan without executing it (no `--yes` needed, even for
`rollback`); `--yes` (on `rollback` only) is the explicit confirmation required
to actually run the destructive plan.

## Verification (tests are first-class)
- golden argv for plan/snapshot/rollback.
- rollback requires --yes (and the gate fires before any runner call).
- a --dry-run preview never calls the runner (even for an unconfirmed rollback).
- layout validation errors.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Never emits a destructive command without explicit confirmation.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Actually touching disks

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
