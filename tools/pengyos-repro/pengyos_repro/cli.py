"""Command-line interface for ``pengyos-repro``.

The argument surface and dispatch are defined and tested here so that the whole
pipeline builds on a *stable, documented* interface (``spec.md`` §Interface):

* ``--version`` — prints the version and exits ``0``.
* ``compare <dirA> <dirB> [--json OUT] [--ignore PATTERN]... [--strip-metadata]``
  — walks both trees, hashes every artifact, classifies each path (identical /
  differs / only-in-A / only-in-B), tolerates known-nondeterministic inputs via
  ``--ignore`` patterns, and prints a human report (or JSON to ``--json``). Pass
  ``--strip-metadata`` to additionally run a second-pass comparison that hides
  the classic nondeterminism heuristics and reports which differences were
  explained away as metadata-only.
* ``score <dirA> <dirB> [--ignore PATTERN]... [--strip-metadata]`` — prints a
  single reproducibility score in ``[0.0, 1.0]`` to stdout.

The pipeline itself (manifest → ignore-filter → classify → score → report) lives
in :func:`run_compare`; the score formula and report schema live in
``pengyos_repro/report.py``.

Exit-code contract (reused across the PengyOS toolset):
``0`` success · ``1`` ran-but-failed · ``2`` usage (argparse) · ``3`` subcommand
recognised but not yet implemented. A ``score``/``compare`` that finds the trees
differ is still a *success* (`0`) — a low score is a valid result, not an error.
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .classify import Diff, classify
from .manifest import ManifestError, manifest
from .patterns import PatternError, filter_manifest, strip_metadata_patterns
from .report import MetadataAnalysis, emit, metadata_analysis, score

# --- Exit codes -------------------------------------------------------------
EXIT_OK: int = 0  # success
EXIT_FAIL: int = 1  # the command ran but produced a failure result
EXIT_USAGE: int = 2  # argparse's default for bad usage
EXIT_NOT_IMPLEMENTED: int = 3  # subcommand recognised but not yet implemented


def _not_implemented(command: str) -> int:
    """Emit the standard not-implemented message and return its exit code."""
    sys.stderr.write(
        f"pengyos-repro: '{command}' is scheduled but not implemented yet "
        ".\n"
    )
    return EXIT_NOT_IMPLEMENTED


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Pure: building the parser has no side effects, which makes it directly
    unit-testable (parsing, defaults, help text).
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-repro",
        description=(
            "Compare two PengyOS builds' outputs; classify differences and "
            "report a reproducibility score and the offending inputs."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the version and exit.",
    )

    sub = parser.add_subparsers(dest="command", metavar="{compare,score}")

    # --- compare ----------------------------------------------------------
    compare_p = sub.add_parser(
        "compare",
        help="Diff two build directories and emit a human/JSON report.",
        description=(
            "Walk <dirA> and <dirB>, hash every artifact, classify each path "
            "(identical / differs / only-in-A / only-in-B), tolerate "
            "known-nondeterministic inputs via --ignore patterns, and print a "
            "human report (or JSON to --json)."
        ),
    )
    compare_p.add_argument(
        "dirA",
        metavar="DIRA",
        help="First build directory (the reference).",
    )
    compare_p.add_argument(
        "dirB",
        metavar="DIRB",
        help="Second build directory (the candidate).",
    )
    compare_p.add_argument(
        "--json",
        dest="json_out",
        metavar="OUT",
        default=None,
        help="Also write the machine-readable report to this path.",
    )
    compare_p.add_argument(
        "--ignore",
        dest="ignore_patterns",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Exclude paths matching this glob (matched against the relative "
            "path). Repeatable. Patterns hide a path from both the report and "
            "the score."
        ),
    )
    compare_p.add_argument(
        "--strip-metadata",
        dest="strip_metadata",
        action="store_true",
        default=False,
        help=(
            "Run a second-pass comparison that additionally hides the classic "
            "nondeterminism heuristics (timestamps, embedded-user, "
            "random-seeds) and reports which differences were explained away as "
            "metadata-only."
        ),
    )

    # --- score ------------------------------------------------------------
    score_p = sub.add_parser(
        "score",
        help="Print a 0.0-1.0 reproducibility score for two build dirs.",
        description=(
            "Compare <dirA> and <dirB> and print a single reproducibility "
            "score in [0.0, 1.0] to stdout: 1.0 means the two trees are "
            "identical, lower scores reflect differing or missing artifacts."
        ),
    )
    score_p.add_argument(
        "dirA",
        metavar="DIRA",
        help="First build directory (the reference).",
    )
    score_p.add_argument(
        "dirB",
        metavar="DIRB",
        help="Second build directory (the candidate).",
    )
    score_p.add_argument(
        "--ignore",
        dest="ignore_patterns",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Exclude paths matching this glob (matched against the relative "
            "path). Repeatable."
        ),
    )
    score_p.add_argument(
        "--strip-metadata",
        dest="strip_metadata",
        action="store_true",
        default=False,
        help=(
            "Print the second-pass (metadata-stripped) score instead of the raw "
            "score: the reproducibility score after hiding the classic "
            "nondeterminism heuristics."
        ),
    )

    return parser


def main(argv: "list[str] | None" = None) -> int:
    """CLI entry point. Returns a process exit code (never raises on good use).

    ``argv`` is accepted for testability and defaults to ``sys.argv[1:]``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"pengyos-repro {__version__}")
        return EXIT_OK

    if not args.command:
        # No subcommand: show help and exit cleanly.
        parser.print_help()
        return EXIT_OK

    if args.command == "compare":
        return _compare(args)

    if args.command == "score":
        return _score(args)

    # Defensive: only ``compare`` and ``score`` are registered subcommands, so
    # this branch is unreachable. Kept so an unexpected command value is a clean,
    # distinct failure rather than a crash.
    return _not_implemented(args.command)


def _run_compare(
    dir_a: str,
    dir_b: str,
    ignore_patterns: "List[str]",
) -> Diff:
    """Walk two trees, apply the ignore filter, and classify them.

    This is the shared pipeline behind ``compare`` and ``score``: build each
    manifest, drop ignored paths from both (so they leave the report and the
    score), then classify the survivors. Raises :class:`ManifestError` if a file
    cannot be read and :class:`PatternError` if an ignore pattern is invalid.
    """
    manifest_a = manifest(dir_a)
    manifest_b = manifest(dir_b)
    filtered_a = filter_manifest(manifest_a, ignore_patterns)
    filtered_b = filter_manifest(manifest_b, ignore_patterns)
    return classify(filtered_a, filtered_b)


def _metadata_analysis(
    dir_a: str,
    dir_b: str,
    ignore_patterns: "List[str]",
    raw_diff: Diff,
) -> "MetadataAnalysis":
    """Run the ``--strip-metadata`` second pass and return its analysis.

    The second pass compares the two trees with the caller's ``ignore_patterns``
    *plus* every curated nondeterminism heuristic, and reports which paths were
    explained away as metadata-only. Raises :class:`PatternError` if the caller's
    patterns are invalid (normally already validated by the first pass).
    """
    stripped_patterns = strip_metadata_patterns(ignore_patterns)
    stripped_diff = _run_compare(dir_a, dir_b, stripped_patterns)
    return metadata_analysis(raw_diff, stripped_diff, ignore_patterns)


def _compare(args: argparse.Namespace) -> int:
    """Implement ``compare``: emit the human report and optional JSON."""
    try:
        diff = _run_compare(args.dirA, args.dirB, args.ignore_patterns)
    except ManifestError as exc:
        sys.stderr.write(f"pengyos-repro: {exc}\n")
        return EXIT_FAIL
    except PatternError as exc:
        sys.stderr.write(f"pengyos-repro: {exc}\n")
        return EXIT_USAGE

    analysis = None
    if args.strip_metadata:
        try:
            analysis = _metadata_analysis(
                args.dirA, args.dirB, args.ignore_patterns, diff
            )
        except PatternError as exc:
            sys.stderr.write(f"pengyos-repro: {exc}\n")
            return EXIT_USAGE

    try:
        emit(
            diff,
            json_out=args.json_out,
            stream=sys.stdout,
            analysis=analysis,
        )
    except OSError as exc:
        sys.stderr.write(f"pengyos-repro: cannot write report: {exc}\n")
        return EXIT_FAIL
    return EXIT_OK


def _score(args: argparse.Namespace) -> int:
    """Implement ``score``: print the reproducibility score.

    Without ``--strip-metadata`` this is the raw score; with it the second-pass
    (metadata-stripped) score.
    """
    try:
        diff = _run_compare(args.dirA, args.dirB, args.ignore_patterns)
    except ManifestError as exc:
        sys.stderr.write(f"pengyos-repro: {exc}\n")
        return EXIT_FAIL
    except PatternError as exc:
        sys.stderr.write(f"pengyos-repro: {exc}\n")
        return EXIT_USAGE

    if args.strip_metadata:
        try:
            analysis = _metadata_analysis(
                args.dirA, args.dirB, args.ignore_patterns, diff
            )
        except PatternError as exc:
            sys.stderr.write(f"pengyos-repro: {exc}\n")
            return EXIT_USAGE
        print(f"{analysis.stripped_score:.4f}")
    else:
        print(f"{score(diff):.{4}f}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via console script
    sys.exit(main())
