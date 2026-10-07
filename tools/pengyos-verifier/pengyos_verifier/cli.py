"""Command-line interface for pengyos-verifier.

Surface:

* ``pengyos-verifier --version`` / ``--help`` — scaffold surface.
* ``pengyos-verifier check --build DIR --attest ATTEST.log [--json OUT]
  [--min-score F] [--pub FILE]`` — run the five checks against a build
  output dir and its attest log, aggregate the findings into one
  report, print the human-readable report to stdout, optionally write
  a machine-readable JSON report to ``OUT``, and exit non-zero when
  any check fails. With ``--pub FILE`` the signatures check also
  verifies the ``.sig`` bodies against the supplied 32-byte ed25519
  public key (the header ``keyid`` must be ``SHA-256(public_key)``);
  a missing or malformed key file is a usage error (exit 2).

Design intent (see ``spec.md``): the verifier is an *external witness* —
it reports findings, it never fixes the build, and it must work offline
against a build output dir + its attest log.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence, Tuple

from pengyos_verifier import __version__, checks, signature_format
from pengyos_verifier.checks_impl import CheckContext, build_report

PROG = "pengyos-verifier"

DESCRIPTION = (
    "Independent witness for PengyOS builds: inspects a build output "
    "directory and its attest log, verifies the attest chain, SBOM "
    "presence, artifact signatures, and repro score, and emits a "
    "pass/fail report with findings."
)


def _add_check_subparser(subparsers) -> None:
    """Register the ``check`` subcommand."""
    check = subparsers.add_parser(
        "check",
        help="Run all checks against a build dir + its attest log.",
        description=(
            "Inspect a build output directory and its attest log, run "
            "the attest-chain / SBOM / signatures / repro-score / "
            "expected-files checks, print a pass/fail report, and exit "
            "non-zero if any check fails."
        ),
    )
    check.add_argument(
        "--build", required=True, metavar="DIR",
        help="Build output directory to inspect.",
    )
    check.add_argument(
        "--attest", default=None, metavar="ATTEST.log",
        help="Path to the attest log (append-only JSONL hash chain). "
             "Not required to live under DIR. If omitted, the attest "
             "check treats the chain as empty and passes.",
    )
    check.add_argument(
        "--json", dest="json_out", default=None, metavar="OUT",
        help="Also write a machine-readable JSON report to OUT.",
    )
    check.add_argument(
        "--min-score", dest="min_score",
        type=float, default=checks.DEFAULT_REPRO_THRESHOLD, metavar="F",
        help=(
            "Minimum repro score for the repro-score check "
            f"(default {checks.DEFAULT_REPRO_THRESHOLD} — a "
            "byte-identical rebuild). Lower it for triage; the witness "
            "default is the SPEC's reproducibility promise."
        ),
    )
    check.add_argument(
        "--pub", dest="pub_key", default=None, metavar="FILE",
        help=(
            "32-byte ed25519 public key (raw bytes, or 64 hex "
            "characters in a text file; whitespace tolerated). When "
            "supplied, the signatures check additionally verifies each "
            "artifact's .sig body with ed25519 and requires the header "
            "keyid to be SHA-256(public_key)."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=DESCRIPTION,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the version and exit.",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    _add_check_subparser(subparsers)
    return parser


def _load_pub_key(path: str) -> Tuple[Optional[bytes], int]:
    """Load the ``--pub`` key file.

    Returns ``(public_key, exit_code)``: ``(None, 0)`` when no key was
    supplied, ``(key, 0)`` on success. A missing or malformed key file
    is a usage error: the reason is printed to stderr and the exit code
    is 2 (the checks never ran — 1 is reserved for "checks ran and one
    failed").
    """
    if path is None:
        return None, 0
    try:
        return signature_format.load_public_key(path), 0
    except FileNotFoundError:
        print(f"error: public key file not found: {path}", file=sys.stderr)
        return None, 2
    except signature_format.PublicKeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None, 2
    except OSError as exc:
        print(f"error: could not read public key file {path}: {exc}",
              file=sys.stderr)
        return None, 2


def _cmd_check(args: argparse.Namespace) -> int:
    """Run the ``check`` subcommand and return the process exit code."""
    public_key, key_err = _load_pub_key(args.pub_key)
    if key_err != 0:
        return key_err
    ctx = CheckContext(
        build_dir=args.build,
        attest_path=args.attest or "",
        repro_threshold=args.min_score,
        public_key=public_key,
    )
    report = build_report(ctx)

    sys.stdout.write(report.render())
    if args.json_out is not None:
        try:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                fh.write(report.to_json())
        except OSError as exc:
            print(f"error: could not write JSON report to "
                  f"{args.json_out}: {exc}", file=sys.stderr)
            return 2
    return report.exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        # No subcommand: print help and exit cleanly (scaffold behavior
        # for a bare invocation).
        parser.print_help(sys.stderr)
        return 0

    if args.command == "check":
        return _cmd_check(args)

    raise SystemExit(f"unknown command: {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
