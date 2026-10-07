"""Command-line interface for ``pengyos-fleet``.

Subcommands are registered as they are implemented so the parser never advertises a command that does not yet exist.
Every command function returns a process exit code; errors are reported to
``stderr`` with a non-zero code.

Implemented so far:

* ``collect`` — collect hardware facts for every host in a list and write one
  declarative hwprofile ``profile.toml`` per machine under ``--out``.
* ``diff`` — compare two profiles and report added / removed / changed devices.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .diff import DiffError, diff_documents, load_profile, render_diff
from .emit import collect_all
from .facts import FactsError
from .hosts import HostsError, load_hosts
from .runner import RunnerError, make_runner


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    Subcommands are added lazily as features are implemented so that the parser
    never advertises a command that does not yet exist.
    """

    parser = argparse.ArgumentParser(
        prog="pengyos-fleet",
        description=(
            "Collect hardware facts for a fleet of hosts and emit one "
            "declarative hwprofile per machine."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    # ``collect`` and ``diff`` are added as each subtask lands.
    subparsers = parser.add_subparsers(
        dest="command",
        metavar="command",
    )

    _add_collect_subparser(subparsers)
    _add_diff_subparser(subparsers)

    return parser


def _add_collect_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``collect`` subcommand (write one profile per host)."""

    collect = subparsers.add_parser(
        "collect",
        help="collect hardware facts and emit one profile per host",
        description=(
            "Collect hardware facts for every host in a host list and write one "
            "declarative hwprofile profile.toml under --out/<name>/."
        ),
    )
    collect.add_argument(
        "--hosts",
        required=True,
        metavar="PATH",
        help="path to the TOML host list (see spec.md -> Host list)",
    )
    collect.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="output directory; one <name>/profile.toml is written per host",
    )
    collect.add_argument(
        "--runner",
        choices=("ssh", "mock"),
        default="ssh",
        help="transport for fact collection (default: ssh)",
    )


def _cmd_collect(args: argparse.Namespace) -> int:
    """Run ``collect``: load the host list and emit a profile per host."""

    try:
        hosts = load_hosts(args.hosts)
        runner = make_runner(args.runner)
    except (HostsError, RunnerError) as exc:
        print(f"pengyos-fleet: {exc}", file=sys.stderr)
        return 2

    try:
        written = collect_all(runner, hosts, args.out)
    except (FactsError, RunnerError) as exc:
        print(f"pengyos-fleet: {exc}", file=sys.stderr)
        return 1

    # ``collect_all`` writes only the hosts whose profile changed since the last
    # run, so a host that is not in the written set was left unchanged. Report
    # each host's outcome rather than silently doing nothing on a re-collect.
    written_names = {path.parent.name for path in written}
    for host in hosts:
        profile = Path(args.out) / host.name / "profile.toml"
        if host.name in written_names:
            print(f"wrote {profile}")
        else:
            print(f"unchanged {profile}")
    return 0


def _add_diff_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``diff`` subcommand (compare two profiles)."""

    diff = subparsers.add_parser(
        "diff",
        help="compare two profiles (added / removed / changed devices)",
        description=(
            "Compare two profile.toml files and report the added (+), removed "
            "(-), and changed (~) devices in the hardware list sections. "
            "metadata/cpu are identity and are not compared."
        ),
    )
    diff.add_argument(
        "a",
        metavar="A",
        help="the baseline profile (first argument)",
    )
    diff.add_argument(
        "b",
        metavar="B",
        help="the updated profile (second argument)",
    )


def _cmd_diff(args: argparse.Namespace) -> int:
    """Run ``diff``: load two profiles and report their hardware differences.

    Exit code ``0`` when the profiles are identical, ``1`` when they differ, and
    ``2`` for bad input (a missing/unreadable file, invalid TOML, or a schema
    violation). The report goes to stdout; errors go to stderr.
    """

    try:
        before = load_profile(args.a)
        after = load_profile(args.b)
    except DiffError as exc:
        print(f"pengyos-fleet: {exc}", file=sys.stderr)
        return 2

    text = render_diff(before, after, label_a=args.a, label_b=args.b)
    print(text, end="")
    return 1 if diff_documents(before, after) else 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 0

    handlers = {
        "collect": _cmd_collect,
        "diff": _cmd_diff,
    }
    return handlers[args.command](args)  # type: ignore[union-attr]


if __name__ == "__main__":
    raise SystemExit(main())
