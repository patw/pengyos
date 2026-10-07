"""Command-line entry point for ``pengyos-kernel-fragments``.

The command surface grows over time (``validate`` / ``compose`` / ``explain``);
each is added by its own implementation task.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .compose import (
    ComposeReport,
    compose_directory,
    render_error,
    render_merged,
    render_warnings,
)
from .explain import ExplainReport, explain_directory, render_explain
from .fragments import SYMBOL_RE
from .validate import (
    REQUIRED_RULES,
    ValidationReport,
    format_report as _format_validation_report,
    select_rules,
    validate_directory,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="pengyos-kernel-fragments",
        description="Validate, compose, and explain PengyOS kernel config "
        "fragments.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print the pengyos-kernel-fragments version and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("version", help="print the version and exit")

    # validate (task 3).
    val = sub.add_parser(
        "validate",
        help="validate a fragments directory (syntax, conflicts, required rules)",
    )
    val.add_argument(
        "fragments_dir",
        help="directory containing the *.fragment files to validate",
    )
    val.add_argument(
        "--profile",
        "-p",
        default=None,
        help="only check the named required-rule (defaults to all rules)",
    )
    val.set_defaults(func=_cmd_validate)

    # compose (task 4).
    comp = sub.add_parser(
        "compose",
        help="compose a profile's fragment list into a merged CONFIG set",
    )
    comp.add_argument(
        "fragments_dir",
        help="directory holding the *.fragment and *.profile files",
    )
    comp.add_argument(
        "--profile",
        "-p",
        required=True,
        help="name of the *.profile to compose",
    )
    comp.add_argument(
        "--out",
        "-o",
        default=None,
        help="write the merged CONFIG set to this file (default: stdout)",
    )
    comp.set_defaults(func=_cmd_compose)

    # explain (task 6).
    expl = sub.add_parser(
        "explain",
        help="explain which fragment(s) set a CONFIG_ symbol and its final value",
    )
    expl.add_argument(
        "symbol",
        help="the CONFIG_ symbol to explain (e.g. CONFIG_VIRTIO)",
    )
    expl.add_argument(
        "dir",
        nargs="?",
        default="./fragments",
        help="directory holding the *.fragment files (default: ./fragments)",
    )
    expl.add_argument(
        "--profile",
        "-p",
        default=None,
        help="report the final value within this profile's merge order",
    )
    expl.set_defaults(func=_cmd_explain)

    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        rules = select_rules(REQUIRED_RULES, args.profile)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    directory = Path(args.fragments_dir)
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 2

    report: ValidationReport = validate_directory(directory, rules=rules)
    print(_format_validation_report(report))
    return report.exit_code


def _cmd_compose(args: argparse.Namespace) -> int:
    directory = Path(args.fragments_dir)
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 2

    report: ComposeReport = compose_directory(directory, args.profile)
    merged = render_merged(report)
    warnings = render_warnings(report)
    error = render_error(report)

    # Conflict warnings and usage errors always go to stderr, so a downstream
    # consumer of stdout gets a clean CONFIG set.
    if warnings:
        print(warnings, file=sys.stderr)
    if error:
        print(error, file=sys.stderr)
        return report.exit_code

    if args.out:
        Path(args.out).write_text(
            merged + ("\n" if merged else ""), encoding="utf-8"
        )
    elif merged:
        print(merged)

    return report.exit_code


def _cmd_explain(args: argparse.Namespace) -> int:
    symbol = args.symbol.strip()
    if not SYMBOL_RE.match(symbol):
        print(
            f"error: {args.symbol!r} is not a CONFIG_ symbol "
            f"(expected CONFIG_<A-Z0-9_>+)",
            file=sys.stderr,
        )
        return 2

    directory = Path(args.dir)
    if not directory.is_dir():
        print(f"error: not a directory: {directory}", file=sys.stderr)
        return 2

    report: ExplainReport = explain_directory(directory, symbol, args.profile)
    if not report.ok:
        print(
            report.error or "error: could not explain symbol",
            file=sys.stderr,
        )
        return report.exit_code

    print(render_explain(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return handler(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
