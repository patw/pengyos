"""Command-line interface for pengyos-desktop-nvidia.

    pengyos-desktop-nvidia lint --repo BR2_EXTERNAL_DIR [--buildroot DIR] [--targets FILE] [--json]

Exit codes: ``0`` clean, ``2`` a lint violation or a user-facing error, ``1`` an
unexpected internal error.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__, lint
from .errors import LintViolation, PengyOSError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-desktop-nvidia",
        description=(
            "Prove the NVIDIA driver (package nvidia-open) is contained to the "
            "opt-in NVIDIA Desktop edition."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    lint_cmd = sub.add_parser("lint", help="check the driver's containment in a BR2_EXTERNAL tree")
    lint_cmd.add_argument("--repo", required=True, metavar="DIR", help="the BR2_EXTERNAL tree")
    lint_cmd.add_argument(
        "--buildroot",
        metavar="DIR",
        help="a Buildroot checkout: also resolve the defconfig through kconfig",
    )
    lint_cmd.add_argument(
        "--targets", metavar="FILE", help="targets.toml to check (default: <repo>/targets.toml, else the repository root's)"
    )
    lint_cmd.add_argument("--json", action="store_true", help="print the report as JSON")
    lint_cmd.set_defaults(func=_cmd_lint)
    return parser


def _cmd_lint(args: argparse.Namespace) -> int:
    try:
        result = lint.lint_variant(args.repo, buildroot=args.buildroot, targets_toml=args.targets)
    except LintViolation as exc:
        if args.json:
            print(json.dumps({"ok": False, "rules": exc.rules, "errors": exc.errors}, indent=2, sort_keys=True))
        for reason in exc.errors:
            print(f"lint: {reason}", file=sys.stderr)
        return 2
    except PengyOSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("lint: ok")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
