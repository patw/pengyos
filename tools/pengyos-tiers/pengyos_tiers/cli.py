"""Command-line interface for pengyos-tiers.

This is the argparse front-end for the 3-tier change policy engine. It owns
argument parsing and dispatch so the individual subcommands can be added without
touching the entry point (``__main__.py``).

Subcommands
-----------
``version``
    Print the pengyos-tiers version and exit.

``classify``
    Classify a change given on the command line
    (``--action``/``--path``/``--target``) against a rules file and print the
    resulting tier + approvals. Informational: exits 0 on success.

``check``
    Classify a change described in a JSON file against a rules file and print
    the result, then **gate**: exit non-zero (distinct code) when the resolved
    tier requires a human signature (``needs_human``). This is the command to
    drop into a pipeline to enforce SPEC §1.5.

Exit codes
----------
    0  success (and, for ``check``, the change does NOT need a human)
    1  error (bad change schema, unreadable/invalid rules file, …)
    3  ``check`` resolved a tier that needs a human signature (gate tripped)

The no-args case prints help to **stderr** and exits 0, matching the sibling
tools in this run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO

from . import __version__
from .change import Change
from .classifier import classify
from .errors import ChangeError, PengyOSTiersError, RulesError
from .rules import RulesFile

# Distinct exit code for the gate tripping on a human-sign tier. Kept separate
# from the error code (1) so a pipeline can tell "needs a human" apart from
# "something went wrong".
EXIT_NEEDS_HUMAN = 3

# The default ruleset shipped with the tool. Resolved relative to this module
# so it is found regardless of the caller's working directory.
_DEFAULT_RULES = Path(__file__).resolve().parents[1] / "tiers.toml"


def _human_readable(cls) -> str:
    """Render a :class:`Classification` as a compact, one-line summary."""

    gate = "GATE: needs human signature" if cls.needs_human else "ok: agent-auto"
    rule = f" [{cls.rule_name}]" if cls.rule_name else ""
    matched = "matched" if cls.matched else "DEFAULT-DENY"
    return (
        f"tier={cls.tier} {matched}{rule}  ->  {gate}"
        f"  ({cls.reason})"
    )


def _emit(cls, as_json: bool, out: TextIO) -> None:
    """Print a classification, either as JSON or a human-readable line."""

    if as_json:
        json.dump(cls.to_dict(), out)
        out.write("\n")
    else:
        print(_human_readable(cls), file=out)


def _load_rules(path: str | None) -> RulesFile:
    """Load a ruleset from ``path`` (or the shipped default)."""

    if path is None:
        path = str(_DEFAULT_RULES)
    return RulesFile.load(path)


def _read_change_json(source: str) -> dict:
    """Read + parse the change JSON from a file path (``-`` = stdin)."""

    if source == "-":
        data = json.load(sys.stdin)
    else:
        path = Path(source)
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError as exc:
            raise ChangeError(f"change file not found: {source}") from exc
        except json.JSONDecodeError as exc:
            raise ChangeError(f"change file is not valid JSON: {exc}") from exc
    return data


# --- subcommand handlers -----------------------------------------------------


def _cmd_version() -> int:
    print(__version__)
    return 0


def _cmd_classify(args: argparse.Namespace) -> int:
    try:
        change = Change.from_dict(
            {"action": args.action, "path": args.path, "target": args.target}
        )
        rules = _load_rules(args.rules)
    except PengyOSTiersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    cls = classify(change, rules)
    _emit(cls, args.json, sys.stdout)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        data = _read_change_json(args.change_json)
        change = Change.from_dict(data)
        rules = _load_rules(args.rules)
    except PengyOSTiersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    cls = classify(change, rules)
    _emit(cls, args.json, sys.stdout)

    # Gate: a human signature is required → trip the gate with a distinct code.
    if cls.needs_human:
        return EXIT_NEEDS_HUMAN
    return 0


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser.

    A single ``subparsers`` block is owned here so every subcommand registers
    against the same namespace.
    """

    parser = argparse.ArgumentParser(
        prog="pengyos-tiers",
        description=(
            "Classify a proposed change into a PengyOS policy tier "
            "(0 = image, 1 = agent-mutable, 2 = human-signed) and gate it."
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
        help="print the pengyos-tiers version and exit",
    )

    p_classify = sub.add_parser(
        "classify",
        help="classify a change (paths/action/target) into a tier + approvals",
        description=(
            "Classify a proposed change given on the command line against a "
            "rules file and print the resulting tier + approvals. Informational "
            "(exits 0 on success)."
        ),
    )
    p_classify.add_argument(
        "--action", required=True, help="action being performed (e.g. 'modify')"
    )
    p_classify.add_argument(
        "--path", required=True, help="primary path touched by the change"
    )
    p_classify.add_argument(
        "--target",
        default=None,
        help="optional named target (package/service/key) a rule may filter on",
    )
    p_classify.add_argument(
        "--rules",
        default=None,
        help="path to a rules file (default: the shipped tiers.toml)",
    )
    p_classify.add_argument(
        "--json",
        action="store_true",
        help="emit the classification as JSON",
    )
    p_classify.set_defaults(func=_cmd_classify)

    p_check = sub.add_parser(
        "check",
        help="classify a change described in JSON against a rules file and gate",
        description=(
            "Classify a change described in a JSON file against a rules file, "
            "print the result, and gate: exit non-zero when the resolved tier "
            "requires a human signature."
        ),
    )
    p_check.add_argument(
        "change_json",
        help="path to a JSON file describing the change ('-' for stdin)",
    )
    p_check.add_argument(
        "--rules",
        default=None,
        help="path to a rules file (default: the shipped tiers.toml)",
    )
    p_check.add_argument(
        "--json",
        action="store_true",
        help="emit the classification as JSON",
    )
    p_check.set_defaults(func=_cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 = success)."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        return _cmd_version()

    if getattr(args, "func", None) is not None:
        return args.func(args)

    # No command given: print help and exit 0.
    parser.print_help(sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
