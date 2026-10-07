"""Command-line interface for pengyos-backup.

This is the argparse front-end for the backup planner. It owns argument parsing
and dispatch so individual subcommands can be added without touching the entry
point (``__main__.py``).

Subcommands
-----------
``version``
    Print the pengyos-backup version and exit.

``plan``
    Emit a systemd timer + oneshot service (restic/rustic golden argv) from a
    ``backup.yaml`` into an output directory. Emits ``pengyos-backup.timer``,
    ``pengyos-backup.service`` (golden argv + ``OnFailure`` alert hook) and the
    ``pengyos-backup-alert.service`` hook.

``verify-restore``
    Restore a snapshot from a fixture repository into a target dir and assert a
    tree hash, proving a documented restore reproduces a known tree.

``render``
    Parse a ``backup.yaml`` and dump the resulting plan as JSON to stdout.
    Accepts ``--json`` (matches the documented interface; output is always
    JSON).

Exit codes
----------
    0  success (including ``verify-restore`` when the tree hash is proven)
    1  error (bad config, unreadable file, a missing/corrupt restore, or a
       tree-hash mismatch)
    2  a required argument was missing (argparse)

The no-args case prints help to **stderr** and exits 0, matching the sibling
tools in this run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import load_config
from .errors import PengyOSBackupError
from .restorer import (
    FIXTURE_REPO,
    FakeRunner,
    load_snapshot,
    restore_argv,
    verify_restore,
)
from .units import emit_units


# --- subcommand handlers -----------------------------------------------------
#
# `version`, `render`, `plan`, and `verify-restore` are implemented.


def _cmd_version() -> int:
    print(__version__)
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    """Emit the systemd timer + service (golden restic/rustic argv) to ``--out``.

    Loads ``backup.yaml`` via :func:`load_config`, renders the units with
    :func:`pengyos_backup.units.emit_units`, and prints a short summary of what
    was written (the emitted filenames and the ``OnFailure`` alert link).
    """

    plan = load_config(args.config)
    units = emit_units(plan, args.binary, Path(args.out))
    for name in units.filenames():
        print(f"wrote {args.out}/{name}")
    print(
        f"backup failure -> OnFailure={units.alert_service_name} "
        f"(alert hook: {args.binary})"
    )
    return 0


def _cmd_verify_restore(args: argparse.Namespace) -> int:
    """Restore a snapshot from a fixture repo and assert its tree hash.

    Restores ``--snapshot`` (default: the repo's snapshot) from ``--repo`` into
    ``--target`` and asserts the restored tree hashes to the snapshot's golden
    value (proving the documented restore reproduces a known tree). With
    ``--dry-run`` it prints the golden restore argv without executing. Returns 1
    on a failed restore or a tree-hash mismatch.
    """

    repo = args.repo
    if not Path(repo).is_dir():
        raise PengyOSBackupError(f"repo not found or not a directory: {repo}")

    # Resolve the snapshot id (default: the repo's own snapshot).
    snapshot_id = args.snapshot
    if snapshot_id is None:
        snapshot_id = load_snapshot(repo).id

    if args.dry_run:
        # Print the golden restore argv the real run would execute.
        print(" ".join(restore_argv(args.binary, snapshot_id, repo, args.target)))
        return 0

    result = verify_restore(
        repo,
        args.target,
        snapshot_id=snapshot_id,
        binary=args.binary,
    )
    if not result.ok:
        print(f"error: {result.error}", file=sys.stderr)
        return 1

    print(result)
    for name in sorted(result.restored):
        print(f"  restored {name}")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    """Parse ``backup.yaml`` and dump the plan as JSON to stdout.

    ``render`` is a pure JSON renderer: it consumes no YAML in the output
    pipeline and emits :meth:`BackupPlan.to_dict` via :mod:`json`. The
    ``--json`` flag matches the documented interface (spec.md) and is accepted
    for parity; output is always JSON.
    """

    plan = load_config(args.config)
    json.dump(plan.to_dict(), sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    A single ``subparsers`` block is owned here so every subcommand registers
    against the same namespace. The argument contract mirrors spec.md's
    Interface section.
    """

    parser = argparse.ArgumentParser(
        prog="pengyos-backup",
        description=(
            "Backup planner (restic/rustic) that emits a systemd timer + "
            "service and proves restore with a tree-hash check (US-SRV-4)."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser(
        "version",
        help="print the pengyos-backup version and exit",
    )

    p_plan = sub.add_parser(
        "plan",
        help="emit a systemd timer + service (restic/rustic golden argv)",
        description=(
            "Emit a systemd timer + oneshot service that runs restic/rustic "
            "from a backup.yaml into an output directory."
        ),
    )
    p_plan.add_argument(
        "--config", required=True, help="path to the backup.yaml planner config"
    )
    p_plan.add_argument(
        "--out", required=True, help="output directory for the emitted units"
    )
    p_plan.add_argument(
        "--binary",
        choices=["restic", "rustic"],
        default="restic",
        help="backup backend to target (default: restic)",
    )
    p_plan.set_defaults(func=_cmd_plan)

    p_verify = sub.add_parser(
        "verify-restore",
        help="restore from a fixture repo and assert a tree hash",
        description=(
            "Restore a snapshot from a fixture repository into a target dir and "
            "assert the restored tree hash. Proves a documented restore "
            "reproduces a known tree."
        ),
    )
    p_verify.add_argument(
        "--repo",
        required=True,
        help="path to a restic/rustic (fixture) repository",
    )
    p_verify.add_argument(
        "--target", required=True, help="directory to restore into"
    )
    p_verify.add_argument(
        "--snapshot",
        default=None,
        help=(
            "snapshot id to restore (default: the repo's own snapshot from "
            "snapshot.json)"
        ),
    )
    p_verify.add_argument(
        "--binary",
        choices=["restic", "rustic"],
        default="restic",
        help="restore backend to target (default: restic)",
    )
    p_verify.add_argument(
        "--dry-run",
        action="store_true",
        help="print the restore argv without executing it",
    )
    p_verify.set_defaults(func=_cmd_verify_restore)

    p_render = sub.add_parser(
        "render",
        help="render the plan (parsed backup.yaml) as JSON",
        description=(
            "Parse a backup.yaml and render the resulting plan as JSON."
        ),
    )
    p_render.add_argument(
        "--config", required=True, help="path to the backup.yaml planner config"
    )
    p_render.add_argument(
        "--json",
        action="store_true",
        help="emit the plan as JSON (render always emits JSON)",
    )
    p_render.set_defaults(func=_cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 = success)."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        return _cmd_version()

    handler = getattr(args, "func", None)
    if handler is not None:
        try:
            return handler(args)
        except PengyOSBackupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    # No command given: print help and exit 0.
    parser.print_help(sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
