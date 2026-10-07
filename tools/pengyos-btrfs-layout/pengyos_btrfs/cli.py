"""Command-line interface for pengyos-btrfs-layout.

The argument contract for every subcommand is defined here (and therefore
locked in by the tests). The subcommands implement the ``spec.md`` Interface:

    pengyos-btrfs plan --out layout.toml
    pengyos-btrfs snapshot <name> [--json] [--dry-run]
    pengyos-btrfs rollback <name> [--yes] [--dry-run]

All three subcommands are fully implemented:

- ``plan`` serializes the canonical layout to ``layout.toml`` (via
  :mod:`pengyos_btrfs.toml_io`) and writes it to ``--out``;
- ``snapshot`` / ``rollback`` build their :class:`Plan` (via
  :mod:`pengyos_btrfs.commands`), execute it (or preview it, or emit it as
  JSON) and print a human-readable step list — through the
  :func:`make_runner` seam (default :class:`RealRunner`; tests substitute a
  mock) and the :mod:`pengyos_btrfs.confirm` gate, so a destructive
  ``rollback`` is never executed without ``--yes``.

Every failure path prints a clean message on stderr and returns a
normalized exit code (see :mod:`pengyos_btrfs.constants`) — never a
traceback. See ``spec.md``, "CLI handlers".
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from . import constants as c
from .commands import (
    BtrfsRunner,
    Plan,
    RealRunner,
    rollback_commands,
    snapshot_commands,
)
from .confirm import execute_or_preview
from .errors import BtrfsCommandError, BtrfsLayoutError, RollbackError
from .layout import canonical_layout
from .toml_io import layout_to_toml

PROG = "pengyos-btrfs"


# --- Argument parser construction ----------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Plan a btrfs subvolume layout (root, home, var, snapshots, …) and "
            "generate create/snapshot/rollback commands. btrfs calls are "
            "mockable; the tool never touches a live filesystem in tests."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"{PROG} {__version__}"
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True  # a subcommand is always required

    plan = sub.add_parser(
        "plan",
        help="Plan a subvolume layout and write it to a file.",
    )
    plan.add_argument(
        "--out",
        required=True,
        metavar="FILE",
        help="Path to write the planned layout (e.g. layout.toml).",
    )

    snapshot = sub.add_parser(
        "snapshot",
        help="Create a named read-only snapshot (preview: --dry-run, "
        "emit JSON: --json).",
    )
    snapshot.add_argument(
        "name",
        metavar="NAME",
        help="Name of the snapshot to create.",
    )
    snapshot.add_argument(
        "--json",
        action="store_true",
        help="Emit the plan as JSON on stdout (generation only — "
        "nothing is executed).",
    )
    snapshot.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the commands without running any of them.",
    )

    rollback = sub.add_parser(
        "rollback",
        help="Roll back to a named snapshot (destructive — requires --yes; "
        "preview: --dry-run).",
    )
    rollback.add_argument(
        "name",
        metavar="NAME",
        help="Name of the snapshot to roll back to.",
    )
    rollback.add_argument(
        c.ROLLBACK_CONFIRM_FLAG,
        action="store_true",
        dest="yes",
        help=(
            "Actually run the rollback. Rollback is destructive (it replaces "
            "live subvolumes) and is refused without this flag."
        ),
    )
    rollback.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the rollback plan without running it (no --yes needed).",
    )

    return parser


# --- Runner seam ---------------------------------------------------------
# The handlers execute plans through a runner. Production uses the real
# ``subprocess``-backed runner; tests substitute a mock by patching this
# factory (see tests/test_cli_handlers.py), which keeps the whole contract
# golden-testable without root or a real btrfs volume.
def make_runner() -> BtrfsRunner:
    """Build the :class:`BtrfsRunner` the handlers execute plans with."""
    return RealRunner()


def format_plan(plan: Plan, *, dry_run: bool = False) -> str:
    """Human-readable rendering of *plan* (a numbered step list).

    Each step prints its description and the exact ``btrfs`` ``argv`` it
    would run. A ``dry_run`` plan gets a header and (via the caller) a
    footer making clear that nothing will be executed.
    """
    total = len(plan.steps)
    header = f"{PROG}: {plan.kind} plan ({total} step(s))"
    if dry_run:
        header += " — dry-run, nothing will be executed"
    lines = [header]
    for i, step in enumerate(plan.steps, 1):
        lines.append(f"  [{i}/{total}] {step.description}")
        lines.append(f"        $ {' '.join(step.argv)}")
    return "\n".join(lines) + "\n"


# --- Subcommand handlers -------------------------------------------------
# Each handler returns a process exit code (see constants).
def cmd_plan(args: argparse.Namespace) -> int:
    """Plan the canonical layout and write it to ``--out`` as ``layout.toml``."""
    layout = canonical_layout()
    try:
        text = layout_to_toml(layout)
    except BtrfsLayoutError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    try:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    except OSError as exc:
        print(
            f"{PROG}: error: could not write {args.out!r}: {exc.strerror or exc}",
            file=sys.stderr,
        )
        return c.EXIT_ERROR
    print(
        f"{PROG}: planned {len(layout.subvolumes)} subvolumes "
        f"(filesystem {layout.filesystem!r}) -> {args.out}"
    )
    return c.EXIT_OK


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Create the named read-only snapshot (or preview/emit its plan).

    ``--json`` is generation-only: it emits ``Plan.to_dict()`` as JSON on
    stdout and never executes. Otherwise the plan runs through the runner
    seam (non-destructive — never gated) unless ``--dry-run`` previews it
    without executing anything.
    """
    layout = canonical_layout()
    try:
        plan = snapshot_commands(layout, args.name)
    except BtrfsLayoutError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2))
        return c.EXIT_OK
    try:
        execution = execute_or_preview(
            make_runner(), plan, confirmed=False, dry_run=args.dry_run
        )
    except BtrfsCommandError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    print(format_plan(plan, dry_run=execution.dry_run), end="")
    if execution.dry_run:
        print(f"{PROG}: dry-run preview — nothing was executed")
    else:
        print(
            f"{PROG}: snapshot {args.name!r}: "
            f"{len(execution.results)} step(s) executed"
        )
    return c.EXIT_OK


def cmd_rollback(args: argparse.Namespace) -> int:
    """Roll back to the named snapshot (or preview its plan).

    Destructive: without ``--yes`` the :mod:`pengyos_btrfs.confirm` gate
    refuses execution *before any runner call* (a clean ``EXIT_ERROR`` on
    stderr; the plan is still shown on stdout so the user sees what
    ``--yes`` would do). ``--dry-run`` previews without executing and never
    needs ``--yes``.
    """
    layout = canonical_layout()
    try:
        plan = rollback_commands(layout, args.name)
    except BtrfsLayoutError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    try:
        execution = execute_or_preview(
            make_runner(), plan, confirmed=args.yes, dry_run=args.dry_run
        )
    except RollbackError as exc:
        # Refused by the gate: show the plan that --yes would run, then the
        # error (which names both escape hatches).
        print(format_plan(plan, dry_run=True), end="")
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    except BtrfsCommandError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return c.EXIT_ERROR
    print(format_plan(plan, dry_run=execution.dry_run), end="")
    if execution.dry_run:
        print(f"{PROG}: dry-run preview — nothing was executed")
    else:
        print(
            f"{PROG}: rollback to {args.name!r}: "
            f"{len(execution.results)} step(s) executed"
        )
    return c.EXIT_OK


_HANDLERS = {
    "plan": cmd_plan,
    "snapshot": cmd_snapshot,
    "rollback": cmd_rollback,
}


# --- Entry point ---------------------------------------------------------
def main(argv=None) -> int:
    """Run the CLI.

    Returns a process exit code (see :mod:`pengyos_btrfs.constants`) rather
    than calling ``sys.exit`` directly, so tests can invoke it without a
    subprocess. ``python -m pengyos_btrfs`` and the console script wrap this
    with ``sys.exit(main(...))``.
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # --help / --version / usage errors
        # argparse has already printed; normalize the exit code.
        return exc.code if isinstance(exc.code, int) else 0

    handler = _HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover — argparse enforces a subcommand
        print(f"{PROG}: error: no command given", file=sys.stderr)
        return c.EXIT_USAGE
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
