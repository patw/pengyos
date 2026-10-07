"""Command-line interface for ``pengyos-fleet-rollout``.

Subcommands are registered eagerly here because the spec
fixes the interface up front (``plan`` / ``apply`` / ``rollback``), but each
handler dispatches to a function in its own module that is filled in by the
matching subtask. Until that subtask lands the handler raises a clear
:class:`~pengyos_fleet_rollout.errors.RolloutError` rather than pretending the
feature works — the scaffold owns the *surface*, the later subtasks own the
*behaviour*.

Implemented so far:

* ``plan`` — read a fleet inventory and produce deterministic, canary-first
  batches, each with an explicit health gate.
* ``apply`` — drive an injectable transport per host, recording a journal;
  dry-run unless ``--commit``.
* ``rollback`` — re-apply every recorded prior artifact in reverse.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .errors import RolloutError, RolloutPlanError


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    The three subcommands are advertised here because the spec fixes the
    interface; the handlers dispatch to ``plan`` / ``apply`` / ``rollback`` in
    their own modules.
    """

    parser = argparse.ArgumentParser(
        prog="pengyos-fleet-rollout",
        description=(
            "Plan, stage and roll back a PengyOS image/update rollout across a "
            "fleet of hosts."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
    )

    _add_plan_subparser(subparsers)
    _add_apply_subparser(subparsers)
    _add_rollback_subparser(subparsers)

    return parser


def _add_plan_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``plan`` subcommand (image + inventory -> batches)."""

    plan = subparsers.add_parser(
        "plan",
        help="plan a staged rollout (canary first) from an inventory",
        description=(
            "Read a fleet inventory plus a built image id and emit a staged "
            "rollout plan: deterministic, canary-first batches, each with an "
            "explicit health gate, and a per-host rollback record."
        ),
    )
    plan.add_argument(
        "--image",
        required=True,
        metavar="IMG",
        help="the built PengyOS image id (or pending update set) to roll out",
    )
    plan.add_argument(
        "--inventory",
        required=True,
        metavar="PATH",
        help="path to the pengyos-fleet-inventory (hosts + role/priority)",
    )
    plan.add_argument(
        "--batch-size",
        type=int,
        default=1,
        metavar="N",
        help="maximum number of hosts per batch (default: 1; the canary)",
    )
    plan.add_argument(
        "--json",
        action="store_true",
        help="emit the plan as JSON on stdout",
    )
    plan.set_defaults(func=_cmd_plan)


def _add_apply_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``apply`` subcommand (drive the transport per host)."""

    apply = subparsers.add_parser(
        "apply",
        help="apply a plan through an injectable transport",
        description=(
            "Apply a plan produced by ``plan`` one batch at a time through an "
            "injectable transport, recording a journal entry before each host. "
            "Dry-run by default (renders the argv it would run); pass "
            "--commit to actually run it. The rollout halts on the first "
            "failing gate."
        ),
    )
    apply.add_argument(
        "--plan",
        required=True,
        metavar="PATH",
        help="path to the plan JSON produced by ``plan``",
    )
    apply.add_argument(
        "--transport",
        choices=("noop", "subprocess"),
        default="noop",
        help="transport to drive per host (default: noop / dry-run)",
    )
    apply.add_argument(
        "--journal",
        required=True,
        metavar="PATH",
        help="path to the rollout journal to append to",
    )
    apply.add_argument(
        "--dry-run",
        dest="commit",
        action="store_false",
        default=False,
        help="render the argv it would run without invoking the transport (the default)",
    )
    apply.add_argument(
        "--commit",
        action="store_true",
        help="actually invoke the transport (opposite of --dry-run)",
    )
    apply.set_defaults(func=_cmd_apply)


def _add_rollback_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``rollback`` subcommand (restore prior artifacts in reverse)."""

    rollback = subparsers.add_parser(
        "rollback",
        help="restore every rolled host's prior artifact, in reverse",
        description=(
            "From a rollout journal, re-apply each rolled host's prior "
            "artifact in reverse order. Idempotent: a host already back is a "
            "no-op. Dry-run by default; pass --commit to actually run it."
        ),
    )
    rollback.add_argument(
        "--journal",
        required=True,
        metavar="PATH",
        help="path to the rollout journal written by ``apply``",
    )
    rollback.add_argument(
        "--transport",
        choices=("noop", "subprocess"),
        default="noop",
        help="transport to drive per host (default: noop / dry-run)",
    )
    rollback.add_argument(
        "--dry-run",
        dest="commit",
        action="store_false",
        default=True,
        help="render the argv it would run without invoking the transport",
    )
    rollback.add_argument(
        "--commit",
        action="store_true",
        help="actually invoke the transport (opposite of --dry-run)",
    )
    rollback.set_defaults(func=_cmd_rollback)


def _cmd_plan(args: argparse.Namespace) -> int:
    """Run ``plan``: read the inventory and emit a staged plan."""

    from . import plan as plan_module

    try:
        produced = plan_module.plan(
            image=args.image,
            inventory_path=args.inventory,
            batch_size=args.batch_size,
        )
    except RolloutPlanError as exc:
        print(f"pengyos-fleet-rollout: {exc}", file=sys.stderr)
        return 2

    if args.json:
        import json

        print(json.dumps(produced.as_dict(), indent=2))
    else:
        print(produced.render_text())
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    """Run ``apply``: drive the transport per host through the plan."""

    from . import apply as apply_module

    try:
        result = apply_module.apply(
            plan_path=args.plan,
            transport=args.transport,
            journal_path=args.journal,
            commit=args.commit,
        )
    except RolloutError as exc:
        print(f"pengyos-fleet-rollout: {exc}", file=sys.stderr)
        return 1

    if not args.commit:
        print("apply: dry run — no transport invoked, journal unchanged")
        return 0

    if result.halted:
        print(
            f"apply: halted on failing gate at {result.failed!r} "
            f"({len(result.applied)} applied, {len(result.untouched)} untouched)"
        )
    else:
        print(f"apply: complete — {len(result.applied)} applied")
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    """Run ``rollback``: restore each rolled host's prior artifact in reverse."""

    from . import rollback as rollback_module

    try:
        result = rollback_module.rollback(
            journal_path=args.journal,
            transport=args.transport,
            commit=args.commit,
        )
    except RolloutError as exc:
        print(f"pengyos-fleet-rollout: {exc}", file=sys.stderr)
        return 1

    if not args.commit:
        print(
            f"rollback: dry run — no transport invoked, journal unchanged "
            f"({len(result.steps)} to restore)"
        )
        return 0

    if result.halted:
        print(
            f"rollback: halted on failing gate at {result.failed!r} "
            f"({len(result.rolled_back)} restored, {len(result.noop)} already back)"
        )
    else:
        print(
            f"rollback: complete — {len(result.rolled_back)} restored, "
            f"{len(result.noop)} already back"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 0

    try:
        return args.func(args)
    except RolloutError as exc:
        print(f"pengyos-fleet-rollout: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
