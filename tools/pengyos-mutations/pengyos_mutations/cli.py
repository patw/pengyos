"""Command-line interface for pengyos-mutations.

The CLI is a thin dispatch layer. Each subcommand handler imports its feature
module lazily so the argument parser can be constructed and unit-tested *before*
the feature modules (``backends``, ``apply``, ``revert``, ``converge``) exist.

Subcommands
-----------
``apply``    snapshot → run command → record (journaled, reversible)
``revert``   restore tracked files for a recorded change
``log``      print the attest log
``converge`` turn a recorded change into a declarative profile patch
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from pengyos_mutations import __version__


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="pengyos-mutations",
        description="Journaled, reversible system changes for PengyOS.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_apply = sub.add_parser(
        "apply",
        help="snapshot -> run command -> record (journaled, reversible)",
    )
    p_apply.add_argument(
        "--desc",
        required=True,
        help="human-readable description of the change being applied",
    )
    p_apply.add_argument(
        "--store",
        default=None,
        help="mutations store directory (default: ~/.config/pengyos/mutations)",
    )
    p_apply.add_argument(
        "--root",
        default=None,
        help="directory to track (snapshot + diff); default: current directory",
    )
    p_apply.add_argument(
        "--cwd",
        default=None,
        help="working directory for the command; default: --root",
    )
    p_apply.add_argument(
        "--backend",
        choices=["mock", "btrfs"],
        default="mock",
        help="snapshot backend (default: mock)",
    )
    p_apply.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="command plus its arguments to run under the snapshot "
        "(a leading '--' is optional and stripped)",
    )

    p_revert = sub.add_parser(
        "revert",
        help="restore tracked files for a recorded change",
    )
    p_revert.add_argument("id", help="the change id to revert")
    p_revert.add_argument(
        "--store",
        default=None,
        help="mutations store directory (default: ~/.config/pengyos/mutations)",
    )

    p_log = sub.add_parser("log", help="print the attest log")
    p_log.add_argument(
        "--json",
        action="store_true",
        help="emit JSON instead of human-readable text",
    )
    p_log.add_argument(
        "--store",
        default=None,
        help="mutations store directory (default: ~/.config/pengyos/mutations)",
    )

    p_converge = sub.add_parser(
        "converge",
        help="turn a recorded change into a declarative profile patch",
    )
    p_converge.add_argument("id", help="the change id to converge")
    p_converge.add_argument(
        "--out",
        required=True,
        help="output path for the generated profile patch",
    )
    p_converge.add_argument(
        "--store",
        default=None,
        help="mutations store directory (default: ~/.config/pengyos/mutations)",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "apply":
        # Imported lazily so the parser can be built in the scaffold stage.
        from pengyos_mutations.apply import apply_change

        command = list(args.cmd)
        # argparse.REMAINDER keeps a leading '--' (if given); drop it so the
        # recorded command is exactly the argv the caller intended.
        if command and command[0] == "--":
            command = command[1:]

        result = apply_change(
            desc=args.desc,
            store=args.store,
            command=command,
            root=args.root,
            cwd=args.cwd,
            backend=args.backend,
        )
        # Propagate the command's exit code so a failed mutation is visible to
        # the shell; an empty command (nothing run) is a success.
        return result.exit_code if result.exit_code is not None else 0

    if args.command == "revert":
        # Imported lazily so the parser can be built in the scaffold stage.
        from pengyos_mutations.revert import RevertError, revert_change

        try:
            result = revert_change(change_id=args.id, store=args.store)
        except RevertError as exc:
            print(f"revert: {exc}", file=sys.stderr)
            return 1
        print(result.summary())
        return 0

    if args.command == "log":
        from pengyos_mutations.attest import dump_log

        return dump_log(json_out=args.json, store=args.store)

    if args.command == "converge":
        # Imported lazily so the parser can be built in the scaffold stage.
        from pengyos_mutations.converge import ConvergeError, converge

        try:
            result = converge(
                change_id=args.id, out=args.out, store=args.store
            )
        except ConvergeError as exc:
            print(f"converge: {exc}", file=sys.stderr)
            return 1
        print(result.summary())
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
