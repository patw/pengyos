# Changelog

All notable changes to `pengyos-btrfs-layout` are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- The `snapshot` and `rollback` subcommands are now implemented (no subcommand
  is left at scaffold stage): `pengyos-btrfs snapshot <name>` builds the
  snapshot plan and runs it (non-destructive, never gated), printing a
  human-readable step list; `--json` emits `Plan.to_dict()` as JSON on stdout
  and executes nothing (generation mode, even with `--dry-run`); `--dry-run`
  previews without running anything. `pengyos-btrfs rollback <name>` is safe
  by default: without `--yes` the confirm gate refuses before any `btrfs`
  call (clean `EXIT_ERROR`, both escape hatches named, and the plan is still
  shown on stdout so the user sees what `--yes` would do); `--dry-run`
  previews without `--yes`; `--yes` executes the 9-step promote-then-swap
  plan. Invalid snapshot names and a failed `btrfs` step are clean
  `EXIT_ERROR`s on stderr (fail-fast, never a traceback). See `spec.md`,
  "CLI handlers".
- `pengyos_btrfs.cli.make_runner`: the runner seam the handlers execute plans
  through (default `RealRunner`; tests substitute a `MockRunner`), and
  `pengyos_btrfs.cli.format_plan`: the golden-tested human-readable step-list
  rendering.
- `tests/test_cli_handlers.py`: golden stdout for the snapshot/rollback
  handlers (execute, `--json`, `--dry-run`, refusal, failure paths), the
  runner-seam default, and the exact `--json` payload
  (`json.dumps(Plan.to_dict(), indent=2) + "\n"`).

### Changed
- `requires-python` raised from `>=3.8` to `>=3.11`: the `layout.toml`
  round-trip uses the stdlib's read-only TOML reader (`tomllib`), which
  requires Python 3.11. Still no third-party dependencies.

### Verified
- The GitHub-ready contract is now green (`tests/test_github_ready.py`, 7
  tests): README.md / LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf") /
  CHANGELOG.md / .gitignore / pyproject.toml / spec.md / implementation.md /
  conftest.py all present and correct, the README's usage block is in sync with
  spec.md's Interface, `.gitignore` is effective (`git check-ignore`), and the
  full suite is green. This was authored at scaffold time and is now confirmed
  passing; no new runtime code.

### Added
- The `plan` subcommand is now implemented: `pengyos-btrfs plan --out
  layout.toml` serializes the canonical layout to a `layout.toml` document
  (via `pengyos_btrfs.toml_io`) and writes it to `--out`, exiting 0 on
  success; an unwritable path is a clean `EXIT_ERROR` on stderr (no
  traceback). Golden + round-trip tested in `tests/test_scaffold.py` and
  `tests/test_toml_io.py`.
- `pengyos_btrfs.toml_io`: the small stdlib `layout.toml` emitter + parser
  (the stdlib has no TOML writer — only read-only `tomllib`).
  `layout_to_toml(layout)` emits a deterministic document: top-level
  `filesystem` / `snapshot_dir` and one `[[subvolumes]]` table per
  subvolume (`name`, `path`, `read_only`, `mount`, `description`); `mount`
  is *omitted* for unmounted subvolumes (TOML has no null).
  `layout_from_toml(text)` parses with `tomllib` (the authoritative reader),
  restores `mount=None` / `description=""` for missing keys, and
  re-validates via `validate_layout`, so
  `layout_from_toml(layout_to_toml(layout)) == layout`; malformed input
  raises `LayoutError` (never a traceback). Byte-for-byte golden for the
  canonical layout; escaping (quotes, backslashes, control chars, UTF-8) is
  pinned in `tests/test_toml_io.py`.
- `pengyos_btrfs.confirm`: the safe-by-default confirmation gate and dry-run
  orchestration for destructive plans. `DESTRUCTIVE_KINDS`
  (`{"rollback"}`), `is_destructive` / `requires_confirmation`, and
  `require_confirmation(kind, *, confirmed, dry_run)` which raises
  `RollbackError` (never a traceback) when a destructive plan is about to be
  executed without explicit `--yes`. `execute_or_preview(runner, plan, *,
  confirmed, dry_run)` enforces the gate *before any runner call* and, in
  `--dry-run` mode, never calls the runner (returning
  `Execution(dry_run=True, results=())` so a rollback can be previewed without
  `--yes`); otherwise it executes the plan via `execute_plan` (fail-fast) and
  returns `Execution(dry_run=False, results=(RunResult, ...))` with an `argvs`
  property. Golden-tested in `tests/test_confirm.py` (no root, no real
  btrfs — execution goes through `MockRunner`).
- `--dry-run` flag on the `snapshot` and `rollback` subcommands and the
  `--yes` confirmation flag on `rollback` (spelled via the
  `constants.ROLLBACK_CONFIRM_FLAG` constant, `dest="yes"`, default off — safe
  by default) in `pengyos_btrfs.cli`.
- `pengyos_btrfs.commands`: pure, golden-testable btrfs command generation.
  `create_commands` / `snapshot_commands` / `rollback_commands` turn a
  validated `Layout` into an immutable `Plan` of `Step`s (a human description
  + exact `btrfs argv`), addressed by a staging mount point (default `/mnt`).
  A bad snapshot name raises `SnapshotError`. `rollback_commands` emits a
  promote-then-swap triplet per subvolume (snapshot → top-level `.rollback`
  sibling → delete live → rename in) to work around `btrfs subvolume rename`
  only spanning one parent.
- `pengyos_btrfs.commands` mockable runner seam: `BtrfsRunner` interface,
  `MockRunner` (records argv; `fail_on` / `results` to simulate failures),
  `RealRunner` (real `subprocess`, opt-in behind `PENGYOS_IT=1`), and
  `execute_plan` which runs a `Plan` in order and raises `BtrfsCommandError`
  at the first non-zero exit.
- `pengyos_btrfs.errors.BtrfsCommandError` (a failed `btrfs` step, carrying the
  failing `argv` / exit code / output) and
  `pengyos_btrfs.constants.DEFAULT_MOUNT_POINT` (`/mnt`).
  Golden-tested in `tests/test_commands.py`.
- `pengyos_btrfs.layout`: the PengyOS canonical subvolume layout defined as
  data — frozen `Subvolume` / `Layout` dataclasses, `canonical_layout()`
  (`@`, `@home`, `@var`, `@snapshots`; flat tree; `@snapshots` is a
  read-only, unmounted snapshot parent directory) and `validate_layout()`
  with precise `LayoutError` messages for every structural rule (name/path
  grammar, uniqueness, mount shadowing, flat-tree, snapshot-dir presence).
  Golden-tested in `tests/test_layout.py`.

## [0.1.0] - 2026-09-16

### Added
- **Repository scaffold**: `pyproject.toml` (setuptools, stdlib-only,
  `pengyos-btrfs` console script), the `pengyos_btrfs` package, `README.md`,
  `LICENSE` (MIT), `CHANGELOG.md`, `.gitignore`, `conftest.py` (pytest harness
  with the `PENGYOS_IT` integration marker) and the `tests/` suite.
- `pengyos_btrfs.constants`: single source of truth for exit codes (0/1/2/3,
  with a distinct `EXIT_NOT_IMPLEMENTED` for scaffolded subcommands), the
  `btrfs` binary name, the `@snapshots` snapshot directory convention and the
  `--yes` rollback confirmation flag name.
- `pengyos_btrfs.errors`: the exception hierarchy (`BtrfsLayoutError` and
  subclasses — `LayoutError` / `SnapshotError` / `RollbackError` /
  `ToolNotFoundError`) so malformed input never surfaces as a traceback.
- `pengyos_btrfs.cli`: the `plan` / `snapshot` / `rollback` subcommand argument
  contract (`plan --out`, `snapshot <name> --json`, `rollback <name>`),
  `--version`, normalized exit codes, and a distinct `EXIT_NOT_IMPLEMENTED`
  dispatch for the not-yet-built handlers.
