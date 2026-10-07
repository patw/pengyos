"""Command-line interface for ``pengyos-install-tui``.

Interface (spec.md):

    pengyos-install-tui run [--answers answers.json] [--json]
    pengyos-install-tui auto --config install.toml [--json]
    pengyos-install-tui install [--payload DIR]

* ``run`` drives the wizard state machine (see
  :mod:`pengyos_install_tui.model`) over stdin/stdout, rendering each step and
  applying answers. It can be replayed non-interactively with ``--answers``
  (a JSON answer set) and emit the resulting plan with ``--json``.
* ``auto`` parses an ``install.toml`` (see
  :mod:`pengyos_install_tui.auto`) into the *same* answer set the interactive
  path produces, and prints it (``--json`` for the machine-readable form). A
  missing/invalid field is a reported error (exit 1), never a crash.

Both handlers are implemented. The ``run`` handler owns the argument surface, the exit-code convention,
and ``--version``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Sequence

from pengyos_install_tui import __version__
from pengyos_install_tui.auto import ConfigError, parse_config, plan_to_json
from pengyos_install_tui.model import ValidationError
from pengyos_install_tui.run import load_answers, run

#: Default name of the file ``auto`` reads when ``--config`` is omitted.
DEFAULT_CONFIG = "install.toml"


class ExitCode:
    """Process exit codes.

    ``USAGE`` (2) is argparse's own exit code and is reserved for it, so a
    handler must never return it for ordinary failures. ``NOT_IMPLEMENTED`` (3)
    is deliberately not a plain error (1): it lets scripts and tests tell "the
    tool ran, but this phase of the build is not implemented yet" apart from
    "the tool ran and something went wrong".
    """

    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_IMPLEMENTED = 3


class NotYetImplementedError(Exception):
    """Raised by a subcommand handler that the scaffold has not filled in yet.

    Carries the offending ``command`` and the task that will implement it, so
    a failing call reads as a pointer to the missing work rather than a crash.
    """

    def __init__(self, command: str, task: str) -> None:
        super().__init__(
            f"'{command}' is not implemented yet: {task}"
        )
        self.command = command
        self.task = task


Handler = Callable[[argparse.Namespace], int]


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--json`` machine-readable-output option."""
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the machine-readable JSON form instead of the human list",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``pengyos-install-tui`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="pengyos-install-tui",
        description=(
            "Guided Core/Server installer wizard + unattended --auto for "
            "PengyOS. Run the interactive wizard (``run``) or drive it from an "
            "install.toml (``auto``)."
        ),
        epilog=(
            "Examples:\n"
            "  pengyos-install-tui run\n"
            "  pengyos-install-tui run --answers answers.json --json\n"
            "  pengyos-install-tui auto --config install.toml\n"
            "  pengyos-install-tui auto --config install.toml --json\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"pengyos-install-tui {__version__}",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    run_p = sub.add_parser(
        "run",
        help="Run the guided installer wizard (stdin/stdout; --answers to replay)",
        description=(
            "Run the guided installer wizard: render each step, apply the "
            "answers, and print the resulting install plan. Replay it "
            "non-interactively with --answers (a JSON answer set) and/or emit "
            "the machine-readable plan with --json."
        ),
    )
    run_p.add_argument(
        "--answers",
        default=None,
        help=f"Path to a JSON answer set to replay instead of prompting "
        f"(default: prompt on stdin)",
    )
    _add_json_option(run_p)
    run_p.set_defaults(handler=_run_handler)

    auto_p = sub.add_parser(
        "auto",
        help="Drive the wizard from an install.toml (unattended)",
        description=(
            "Parse an install.toml into the same answer set the interactive "
            "wizard produces, and print the resulting plan. Use --json for the "
            "machine-readable form. A missing or invalid field is a reported "
            "error, not a crash."
        ),
    )
    auto_p.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Path to the install.toml (default: {DEFAULT_CONFIG})",
    )
    _add_json_option(auto_p)
    auto_p.set_defaults(handler=_auto_handler)

    install_p = sub.add_parser(
        "install",
        help="Install PengyOS from this medium onto a disk (ERASES it)",
        description=(
            "The interactive console installer: pick a disk, a computer name and "
            "an account, confirm, and install the medium's image through the "
            "pengyos-install engine (the same path as the GUI). run/auto only "
            "produce an answer set; this erases the chosen disk."
        ),
    )
    install_p.add_argument(
        "--payload",
        default=None,
        help="payload directory (default: the live medium's /run/pengyos/payload)",
    )
    install_p.set_defaults(handler=_install_handler)

    return parser


def _install_handler(ns: argparse.Namespace) -> int:
    """The ``install`` handler: the real, disk-erasing console installer."""
    from pengyos_install_tui.install import run_install

    return run_install(payload=ns.payload)


def _run_handler(ns: argparse.Namespace) -> int:
    """The ``run`` handler.

    Drives the interactive wizard (rendering each step, applying answers) unless
    ``--answers`` is given, in which case it replays a JSON answer set. Prints the
    resulting plan (``--json`` for the machine-readable form). A missing/invalid
    field, an unreadable answers file, or an interactive EOF before confirmation
    is a reported error (exit 1), never a crash.
    """
    answers = None
    if ns.answers is not None:
        try:
            answers = load_answers(ns.answers)
        except ConfigError as exc:
            print(f"pengyos-install-tui: {exc.message}", file=sys.stderr)
            return ExitCode.ERROR
    try:
        plan = run(answers=answers)
    except (ConfigError, ValidationError) as exc:
        # --answers replay raises ConfigError (missing field) / ValidationError
        # (bad value); the interactive path re-prompts instead of raising.
        message = getattr(exc, "message", str(exc))
        print(f"pengyos-install-tui: {message}", file=sys.stderr)
        return ExitCode.ERROR
    if plan is None:
        # Interactive EOF at a prompt before a plan was confirmed.
        print("pengyos-install-tui: no plan confirmed (EOF before confirmation)",
              file=sys.stderr)
        return ExitCode.ERROR
    if ns.json:
        print(plan_to_json(plan))
    else:
        print("\n".join(plan.as_lines()))
    return ExitCode.OK


def _auto_handler(ns: argparse.Namespace) -> int:
    """The ``auto`` handler.

    Parses ``--config`` (an ``install.toml``) into the same :class:`Plan` the
    interactive path produces and prints it (``--json`` for the machine-readable
    form). A missing/invalid field is a reported error (exit 1), never a crash.
    """
    try:
        plan = parse_config(ns.config)
    except (ConfigError, ValidationError) as exc:
        print(f"pengyos-install-tui: {exc.message}", file=sys.stderr)
        return ExitCode.ERROR
    if ns.json:
        print(plan_to_json(plan))
    else:
        print("\n".join(plan.as_lines()))
    return ExitCode.OK


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code (tests call this
    directly; ``__main__`` turns it into ``sys.exit``)."""
    parser = build_parser()
    ns = parser.parse_args(list(argv) if argv is not None else None)
    handler: Handler = ns.handler
    try:
        return handler(ns)
    except NotYetImplementedError as exc:
        print(f"pengyos-install-tui: {exc}", file=sys.stderr)
        return ExitCode.NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
