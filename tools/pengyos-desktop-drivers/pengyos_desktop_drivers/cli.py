"""Command-line interface for pengyos-desktop-drivers.

Two subcommands, matching the spec's interface::

    pengyos-desktop-drivers emit --out DIR [--json]
    pengyos-desktop-drivers lint --fragment FILE --firmware FILE [--json]

Exit codes:

* ``0`` -- success (emit wrote its artifacts; lint was clean).
* ``2`` -- expected, user-facing failure (a lint violation, or a feature not yet
  implemented). The message comes from :class:`PengyOSError`.
* ``1`` -- unexpected internal error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from . import __version__, emit, lint
from .errors import PengyOSError, LintViolation


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with ``emit`` and ``lint`` subcommands."""
    parser = argparse.ArgumentParser(
        prog="pengyos-desktop-drivers",
        description=(
            "Generic (no vendor-acceleration) PengyOS Desktop kernel fragment "
            "and linux-firmware profile, with a linter."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    emit_cmd = sub.add_parser(
        "emit",
        help="render the Desktop linux.fragment and firmware.list into --out",
        description="Render the Desktop linux.fragment and firmware.list into --out.",
    )
    emit_cmd.add_argument(
        "--out", required=True, metavar="DIR", help="output directory to write into"
    )
    emit_cmd.add_argument(
        "--json",
        action="store_true",
        help="emit the write report as JSON on stdout",
    )
    emit_cmd.set_defaults(func=_cmd_emit)

    lint_cmd = sub.add_parser(
        "lint",
        help="validate a fragment + firmware list against the profile rules",
        description=(
            "Validate a kernel fragment and firmware list: symbol existence, "
            "no vendor-acceleration symbols, firmware subset, and class coverage."
        ),
    )
    lint_cmd.add_argument(
        "--fragment", required=True, metavar="FILE", help="the linux.fragment to lint"
    )
    lint_cmd.add_argument(
        "--firmware", required=True, metavar="FILE", help="the firmware.list to lint"
    )
    lint_cmd.add_argument(
        "--json",
        action="store_true",
        help="emit the lint report as JSON on stdout",
    )
    lint_cmd.set_defaults(func=_cmd_lint)

    return parser


def _cmd_emit(args: argparse.Namespace) -> int:
    try:
        result = emit.emit_desktop_profile(args.out, as_json=args.json)
    except PengyOSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        frag = result["fragment"]
        fw = result["firmware"]
        print(f"wrote {frag['path']} ({frag['bytes']} bytes)")
        print(f"wrote {fw['path']} ({fw['lines']} lines)")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    try:
        result = lint.lint_fragment(args.fragment, args.firmware, as_json=args.json)
    except LintViolation as exc:
        for reason in exc.errors:
            print(f"lint: {reason}", file=sys.stderr)
        return 2
    except PengyOSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("lint: ok" if result["ok"] else "lint: failed")
    return 0 if result["ok"] else 2


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
