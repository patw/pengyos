"""Command-line interface for ``pengyos-update-triage``.

Current state
-------------
The full interface is real and working (see ``spec.md`` §Interface):

* ``--version`` — prints the version.
* ``run --pins PATH --advisories DIR --changelogs DIR --out PATH
  [--min-severity LEVEL]`` — drives the whole pipeline (ingest → match →
  changelog scan → classify → rank → ``--min-severity`` filter), writes
  ``proposals.json`` to ``--out`` and prints a human summary. A run with zero
  proposals is a success (exit ``0``); bad/missing input is a ran-but-failed
  exit (``1``). The logic lives in :mod:`.proposal`.
* ``explain <proposal-id> [--proposals PATH]`` — reads a previously written
  ``proposals.json`` (default ``proposals.json``) and prints the full
  rule-by-rule rationale chain for that id, highlighting the rule that fired.
  Unknown ids or a missing file are a clean ``1``.

Bad usage (missing required flags, unknown subcommand, an invalid
``--min-severity`` value) is a usage error (exit ``2``) raised by argparse.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, proposal
from .models import SEVERITY_LEVELS  # noqa: F401  (re-exported; see below)

# --- Exit codes -------------------------------------------------------------
EXIT_OK: int = 0  # success
EXIT_FAIL: int = 1  # the command ran but produced a failure result
EXIT_USAGE: int = 2  # argparse's default for bad usage
EXIT_NOT_IMPLEMENTED: int = 3  # subcommand recognised but not yet implemented

# --- Documented interface constants -----------------------------------------
# The severity *vocabulary* and its *ordering* live in :mod:`.models`
# (``SEVERITY_LEVELS``, ``SEVERITY_NONE``, ``severity_rank``) because they are a
# domain concept shared by classification (item 5) and ranking (item 6); this
# module re-exports :data:`SEVERITY_LEVELS` for the ``--min-severity`` choices
# and keeps the CLI-only default below.
DEFAULT_MIN_SEVERITY: str = "low"

# The default location `explain` reads a previously-written proposals.json from.
DEFAULT_PROPOSALS_FILE: str = "proposals.json"


def _not_implemented(command: str) -> int:
    """Emit the standard not-implemented message and return its exit code."""
    sys.stderr.write(
        f"pengyos-update-triage: '{command}' is scheduled but not implemented "
        "yet.\n"
    )
    return EXIT_NOT_IMPLEMENTED


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Pure: building the parser has no side effects, which makes it directly
    unit-testable (parsing, defaults, help text).
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-update-triage",
        description=(
            "Ingest OSV/NVD advisories and upstream changelogs for pinned "
            "PengyOS components; classify each finding with a transparent "
            "rationale and emit ranked, justified update proposals."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version and exit.",
    )

    sub = parser.add_subparsers(dest="command", metavar="{run,explain}")

    run_p = sub.add_parser(
        "run",
        help="Ingest advisories + changelogs, classify, and emit proposals.",
        description=(
            "Read the pins manifest, the advisory fixtures, and the changelog "
            "fixtures; classify each finding (security/relevant/ignore) with a "
            "rationale; rank the survivors; and write the proposals."
        ),
    )
    run_p.add_argument(
        "--pins",
        required=True,
        metavar="PATH",
        help="Path to the pins manifest (TOML) listing the pinned components.",
    )
    run_p.add_argument(
        "--advisories",
        required=True,
        metavar="DIR",
        help="Directory of OSV/NVD advisory fixtures (JSON).",
    )
    run_p.add_argument(
        "--changelogs",
        required=True,
        metavar="DIR",
        help="Directory of upstream changelog fixtures.",
    )
    run_p.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="Path to write the emitted proposals.json.",
    )
    run_p.add_argument(
        "--min-severity",
        default=DEFAULT_MIN_SEVERITY,
        choices=SEVERITY_LEVELS,
        metavar="LEVEL",
        help="Only emit proposals at or above this severity (default: "
        "%(default)s).",
    )

    explain_p = sub.add_parser(
        "explain",
        help="Print the full rationale chain for one proposal.",
        description=(
            "Given a proposal id, print the complete rule-by-rule rationale "
            "and the evidence (source ids) behind its decision."
        ),
    )
    explain_p.add_argument(
        "proposal_id",
        metavar="PROPOSAL-ID",
        help="The id of the proposal to explain.",
    )
    explain_p.add_argument(
        "--proposals",
        default=DEFAULT_PROPOSALS_FILE,
        metavar="PATH",
        help="Path to a proposals.json previously written by 'run' to read the "
        "rationale chain from (default: %(default)s).",
    )

    return parser


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point. Returns a process exit code (never raises on good use).

    ``argv`` is accepted for testability and defaults to ``sys.argv[1:]``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"pengyos-update-triage {__version__}")
        return EXIT_OK

    if not args.command:
        # No subcommand: show help and exit cleanly.
        parser.print_help()
        return EXIT_OK

    if args.command == "run":
        return proposal.run_command(
            args.pins,
            args.advisories,
            args.changelogs,
            args.out,
            min_severity=args.min_severity,
        )

    if args.command == "explain":
        return proposal.explain_command(args.proposals, args.proposal_id)

    # Defensive: only ``run`` and ``explain`` are registered subcommands, so
    # this branch is unreachable. Kept so an unexpected command value is a
    # clean, distinct failure rather than a crash.
    return _not_implemented(args.command)


if __name__ == "__main__":  # pragma: no cover - exercised via console script
    sys.exit(main())
