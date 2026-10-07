"""Command-line interface for PengyOS repro-report.

Subcommands
-----------
``diff``  Compare two pengyos-build inputs and write ``repro-report.json`` to
          the conventional witness path. Each input is a build dir (resolved via
          its ``build-manifest.json`` first, then a content-hash walk of
          ``images/``) or a ``build-manifest.json`` file directly.
``score`` Read a report back and print its reproducibility score.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, core


# Re-export core's error so the CLI can raise and catch a single ReproReportError
# type (mapped to a clean non-zero exit, no traceback).
ReproReportError = core.ReproReportError


def _add_diff_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--a",
        required=True,
        help="First build input: a build dir (uses its build-manifest.json, "
        "falling back to a content-hash walk of images/) or a build-manifest.json file.",
    )
    parser.add_argument(
        "--b",
        required=True,
        help="Second build input: a build dir (uses its build-manifest.json, "
        "falling back to a content-hash walk of images/) or a build-manifest.json file.",
    )
    parser.add_argument(
        "--out",
        required=False,
        default=None,
        help="Where to write repro-report.json (e.g. <build>/images/repro-report.json).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report JSON to stdout instead of a one-line summary.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero (a clean ReproReportError -> exit 1, no traceback) when the "
        "reproducibility score is below 1.0, so CI can gate on reproducibility.",
    )


def _add_score_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report", required=True, help="Path to a repro-report.json to read.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero (a clean ReproReportError -> exit 1, no traceback) when the "
        "reproducibility score is below 1.0, so CI can gate on reproducibility.",
    )


def _resolve_out(args: argparse.Namespace) -> str:
    """The path to write the report to.

    ``--out`` wins when given. Otherwise the report is written to the
    conventional witness path under ``--a``'s ``images/`` directory, so a
    ``diff`` with no explicit ``--out`` lands exactly where the witness reads.
    A non-directory ``--a`` (a manifest file, or a missing path) has no
    conventional witness path, so ``--out`` is required there.
    """
    if args.out is not None:
        return args.out
    if os.path.isdir(args.a):
        return core.witness_path(args.a)
    raise ReproReportError(
        "missing required argument --out: --a is not a build dir, so the "
        "conventional witness path (<build>/images/repro-report.json) cannot "
        "be derived; pass --out explicitly"
    )


def cmd_diff(args: argparse.Namespace) -> int:
    out_path = _resolve_out(args)
    # resolve_source handles both build dirs (manifest primary, images/ walk
    # fallback) and manifest files, raising ReproReportError for missing inputs.
    comparison = core.compare_sources(args.a, args.b)
    report = core.build_report(comparison)
    # Write the report first so the witness can read it even when strict mode
    # later fails: the report is the artifact the witness consumes, and the
    # exit code is what gates CI.
    core.write_report(report, out_path)
    _enforce_strict(args.strict, report["score"])

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(core.summarize(comparison))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    report = core.load_report(args.report)
    score = core.compute_score(report)
    _enforce_strict(args.strict, score)
    print(f"{score:.4f}")
    return 0


def _enforce_strict(strict: bool, score: float) -> None:
    """Raise a clean ``ReproReportError`` when ``--strict`` and ``score < 1.0``.

    ``--strict`` lets CI gate on reproducibility: a score below 1.0 means at
    least one artifact was classified ``real`` (a genuine reproducibility
    failure). The failure surfaces as the single ``ReproReportError`` that the
    CLI maps to a clean non-zero exit (exit 1, no traceback) rather than a raw
    exception.
    """
    if strict and score < 1.0:
        raise ReproReportError(
            f"reproducibility below threshold (score={score:.4f} < 1.0): "
            "at least one artifact is a real difference; "
            "rerun without --strict to inspect the full report"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-repro-report",
        description="Rebuild-and-compare reproducibility report for PengyOS build outputs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    diff_parser = subparsers.add_parser("diff", help="Compare two build dirs and write repro-report.json.")
    _add_diff_args(diff_parser)
    diff_parser.set_defaults(func=cmd_diff)

    score_parser = subparsers.add_parser("score", help="Read a report and print its score.")
    _add_score_args(score_parser)
    score_parser.set_defaults(func=cmd_score)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ReproReportError as exc:
        # The single, user-facing failure type: a clean one-line message and a
        # non-zero exit, never a traceback. Covers missing/unreadable manifests
        # and dirs (via ``core.resolve_source``/``load_manifest``) as well as
        # missing/unreadable or malformed reports (via ``core.load_report``).
        print(f"pengyos-repro-report: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
