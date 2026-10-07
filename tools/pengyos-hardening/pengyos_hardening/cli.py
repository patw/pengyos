"""Command-line interface for the ``pengyos_hardening`` tool.

Implements the interface declared in spec.md:

    pengyos-hardening baseline [--json]
    pengyos-hardening check --tree REPO_ROOT [--json OUT]
    pengyos-hardening check --tree REPO_ROOT --rule ID [--json]

The ``baseline`` and ``check`` subcommands delegate to the ``baseline`` and
``check`` submodules. Those submodules are authored in later implementation
subtasks; the CLI is the stable dispatch surface that
stays put across them. Until a submodule is implemented, invoking it reports the
gap with a clear message rather than crashing with a traceback.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser (subcommands: baseline, check)."""
    parser = argparse.ArgumentParser(
        prog="pengyos-hardening",
        description=(
            "Audit BR2_EXTERNAL rootfs-overlays and defconfigs against the "
            "PengyOS hardening baseline and report every deviation by rule id."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,  # no bare `pengyos-hardening` with no subcommand
    )

    p_baseline = sub.add_parser(
        "baseline",
        help="load and structurally validate the committed hardening baseline",
    )
    p_baseline.add_argument(
        "--json",
        action="store_true",
        help="emit the validated baseline as JSON",
    )
    p_baseline.add_argument(
        "--path",
        action="store",
        default=None,
        metavar="FILE",
        help="override the baseline data file (defaults to the committed one)",
    )
    p_baseline.set_defaults(handler=_handle_baseline)

    p_check = sub.add_parser(
        "check",
        help="audit a repo tree against the baseline",
    )
    p_check.add_argument(
        "--tree",
        required=True,
        metavar="REPO_ROOT",
        help="path to the repository root to audit",
    )
    p_check.add_argument(
        "--rule",
        metavar="ID",
        help="restrict the audit to a single rule id",
    )
    p_check.add_argument(
        "--json",
        nargs="?",
        const="-",
        default=None,
        metavar="OUT",
        help=(
            "emit JSON findings to stdout (or to OUT if given; '-' means stdout)"
        ),
    )
    p_check.set_defaults(handler=_handle_check)

    return parser


def _handle_baseline(args: argparse.Namespace) -> int:
    """Dispatch the ``baseline`` subcommand to the baseline submodule."""
    try:
        from . import baseline
    except ImportError as exc:  # pragma: no cover - guarded by tests
        print(
            f"pengyos-hardening: baseline module not implemented yet: {exc}",
            file=sys.stderr,
        )
        return 3
    return baseline.main(args)


def _handle_check(args: argparse.Namespace) -> int:
    """Dispatch the ``check`` subcommand to the check submodule."""
    try:
        from . import check
    except ImportError as exc:  # pragma: no cover - guarded by tests
        print(
            f"pengyos-hardening: check module not implemented yet: {exc}",
            file=sys.stderr,
        )
        return 3
    return check.main(args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Entry point: parse arguments and dispatch to the subcommand handler."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
