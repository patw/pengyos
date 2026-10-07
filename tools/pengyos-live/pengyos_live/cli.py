"""Command-line surface for ``pengyos-live``.

Two subcommands, per ``spec.md``:

* ``emit --edition desktop|core|server --session cosmic|console --out DIR
  [--json]`` — render the live-session overlay for an edition + session into
  ``--out``.
* ``lint --overlay DIR [--json]`` — check an emitted overlay (installer present
  AND autostarted, autologin + live marker present, no pre-emptive disk write).

Both handlers are implemented: ``emit`` writes the overlay and returns
``EXIT_OK``; ``lint`` runs every rule and returns ``EXIT_OK`` for a clean
overlay or ``EXIT_FAIL`` when any rule is violated.

Exit codes (shared with the rest of the tool):

* ``EXIT_OK`` (0) — success
* ``EXIT_FAIL`` (1) — a lint rule failed / runtime error
* ``EXIT_USAGE`` (2) — bad command-line usage (argparse)
* ``EXIT_NOT_IMPLEMENTED`` (3) — reserved; the subcommand is wired but its
  logic is not yet implemented
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import emit
from . import lint

# --- exit codes --------------------------------------------------------------

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NOT_IMPLEMENTED = 3

# --- edition / session choices (documented in spec.md) -----------------------

EDITIONS = ("desktop", "core", "server")
SESSIONS = ("cosmic", "console")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser (``emit`` / ``lint`` subcommands)."""
    parser = argparse.ArgumentParser(
        prog="pengyos-live",
        description=(
            "Emit + lint the PengyOS live/installer session overlay (boot to a "
            "desktop, installer running)."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version and exit.",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # --- emit ----------------------------------------------------------------
    emit = subparsers.add_parser(
        "emit",
        help="render the live-session overlay for an edition + session",
        description=(
            "Render the live-session overlay (autologin drop-in, installer "
            "autostart, live marker) for an edition + session into --out."
        ),
    )
    emit.add_argument(
        "--edition",
        required=True,
        choices=EDITIONS,
        help="edition to build the overlay for",
    )
    emit.add_argument(
        "--session",
        required=True,
        choices=SESSIONS,
        help="session the installer autostarts into (cosmic or console)",
    )
    emit.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="output directory for the emitted overlay",
    )
    emit.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON summary instead of a human-readable one",
    )

    # --- lint ----------------------------------------------------------------
    lint = subparsers.add_parser(
        "lint",
        help="lint an emitted overlay",
        description=(
            "Check an emitted overlay: the installer is present AND autostarted "
            "for the chosen session, autologin + live marker present, and the "
            "live path does not write the target disk before the user confirms."
        ),
    )
    lint.add_argument(
        "--overlay",
        required=True,
        metavar="DIR",
        help="directory containing an emitted overlay to lint",
    )
    lint.add_argument(
        "--json",
        action="store_true",
        help="emit a JSON report instead of a human-readable one",
    )

    return parser


def _emit(args: argparse.Namespace) -> int:
    """Emit the live overlay for an edition + session into ``--out``.

    Renders the overlay (autologin drop-in, installer autostart, live marker)
    by writing the model's files to ``--out`` (absolute target paths flattened
    relative to the output root), then prints a human summary or, with
    ``--json``, a machine-checkable summary. A bad edition/session is a usage
    error (exit 2); any filesystem error is a runtime failure (exit 1).
    """
    try:
        overlay = emit.emit_from_strings(args.edition, args.session, args.out)
    except ValueError as exc:
        # Unknown edition/session. argparse `choices` already rejects these
        # before we get here, so this is defensive — never a usage error the
        # CLI surface can produce.
        print(f"pengyos-live emit: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        written = emit.write_overlay(overlay, args.out)
    except OSError as exc:
        print(f"pengyos-live emit: cannot write to {args.out}: {exc}", file=sys.stderr)
        return EXIT_FAIL

    if args.json:
        print(emit.summary_to_json(emit.summary_from_written(overlay, written)))
    else:
        print(
            f"emitted {len(written)} live-overlay file(s) for "
            f"{args.edition}/{args.session} into {args.out}:"
        )
        for path in written:
            print(f"  {path}")

    return EXIT_OK


def _lint(args: argparse.Namespace) -> int:
    """Lint the overlay at ``--overlay`` against every live-overlay rule.

    Reads the emitted overlay back from ``--overlay`` (the flattened tree
    :func:`pengyos_live.emit` wrote), runs each rule, and prints a human
    summary or, with ``--json``, a machine-checkable report. A clean overlay
    returns ``EXIT_OK`` (0); any failed rule returns ``EXIT_FAIL`` (1); a
    filesystem error is ``EXIT_FAIL`` too.
    """
    try:
        report = lint.lint_directory(args.overlay)
    except (OSError, NotADirectoryError) as exc:
        print(f"pengyos-live lint: cannot read {args.overlay}: {exc}", file=sys.stderr)
        return EXIT_FAIL

    if report.ok:
        if args.json:
            print(lint.report_to_json(report))
        else:
            print(
                f"OK: overlay at {args.overlay} is a valid live overlay for "
                f"{report.session.value_str} (installer autostarted, autologin "
                "and live marker present, no pre-emptive disk write)."
            )
        return EXIT_OK

    if args.json:
        print(lint.report_to_json(report))
    else:
        print(
            f"FAIL: overlay at {args.overlay} for session "
            f"{report.session.value_str if report.session is not None else 'unknown'}",
            file=sys.stderr,
        )
        for finding in report.findings:
            print(f"  [{finding.rule.value}] {finding.detail}", file=sys.stderr)
    return EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"pengyos-live {__version__}")
        return EXIT_OK

    if args.command is None:
        parser.print_help()
        return EXIT_OK

    if args.command == "emit":
        return _emit(args)
    if args.command == "lint":
        return _lint(args)

    # argparse `choices` rejects unknown commands before we get here.
    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
