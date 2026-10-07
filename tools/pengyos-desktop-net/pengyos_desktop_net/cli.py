"""CLI for pengyos_desktop_net: the ``emit`` and ``lint`` lanes.

    pengyos-desktop-net emit --out DIR [--json]
    pengyos-desktop-net lint --overlay DIR [--json]

Runnable as ``pengyos-desktop-net`` (console script) or
``python -m pengyos_desktop_net``.
"""

from __future__ import annotations

import argparse
import sys

from pengyos_desktop_net import __version__
from pengyos_desktop_net import emit as emit_mod
from pengyos_desktop_net import lint as lint_mod


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-desktop-net",
        description=(
            "Declarative NetworkManager + wpa_supplicant + COSMIC panel "
            "defaults for the PengyOS Desktop."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    emit_p = sub.add_parser(
        "emit",
        help="render the network defaults as build-consumed files",
    )
    emit_p.add_argument(
        "--out",
        required=False,
        help="directory to write the emitted overlay into "
        "(omit with --json, which only prints)",
    )
    emit_p.add_argument(
        "--json",
        action="store_true",
        help="print the emitted artifacts as JSON instead of writing files",
    )

    lint_p = sub.add_parser(
        "lint",
        help="check an emitted/installed overlay for the spec's invariants",
    )
    lint_p.add_argument(
        "--overlay",
        required=True,
        help="the overlay directory to lint",
    )
    lint_p.add_argument(
        "--json",
        action="store_true",
        help="print the result as JSON",
    )

    return parser


def _cmd_emit(args: argparse.Namespace) -> int:
    if args.json:
        print(emit_mod.emit_json())
        return 0
    if not args.out:
        print("error: --out is required unless --json is given", file=sys.stderr)
        return 2
    artifacts = emit_mod.emit(args.out)
    for art in artifacts:
        rel = art.path.lstrip("/")
        print(f"wrote {rel}")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    if args.json:
        print(lint_mod.lint_json(args.overlay))
    else:
        result = lint_mod.lint_overlay(args.overlay)
        if result.ok:
            print("OK: overlay passes all checks")
        else:
            print(f"FAIL: {len(result.violations)} violation(s):")
            for message in result.messages:
                print(f"  - {message}")
    return 0 if lint_mod.lint_overlay(args.overlay).ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "emit":
        return _cmd_emit(args)
    if args.command == "lint":
        return _cmd_lint(args)
    parser.error(f"unknown command {args.command!r}")
    return 2  # unreachable


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
