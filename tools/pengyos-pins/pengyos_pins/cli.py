"""Command-line entry point for ``pengyos-pins``.

The subcommand surface grows over time (``validate`` / ``emit`` / ``lock`` /
``drift``); each is added by its own implementation task.  The ``validate``
subcommand enforces the whole manifest contract: required fields (via the schema
loader) plus the semantic rules (SPDX license syntax and sha256 hash format).
``lock`` writes a resolved lockfile from a validated manifest; ``drift`` diffs a
working manifest against that lockfile.

Exit codes: ``0`` success, ``1`` the pins file failed validation, ``2`` a usage
error (argparse convention).
"""

from __future__ import annotations

import argparse
import sys
import tomllib

from . import __version__
from .emit import ALL_FORMATS, emit
from .lockfile import (
    DEFAULT_LOCKFILE,
    drift,
    format_drift,
    load_lockfile,
    write_lockfile,
)
from .schema import PinsError, load_pins
from .validate import validate_pins


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="pengyos-pins",
        description="Pinned component manifest for PengyOS: validate pins.toml, "
        "emit a Buildroot fragment + report, and detect drift against a lockfile.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="print the pengyos-pins version and exit",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    sub.add_parser("version", help="print the pengyos-pins version and exit")

    p_validate = sub.add_parser(
        "validate",
        help="validate a pins.toml file (required fields, SPDX, hash format)",
    )
    p_validate.add_argument(
        "path",
        metavar="PINS.TOML",
        nargs="?",
        default="pins.toml",
        help="path to the pins.toml to validate (default: pins.toml)",
    )

    p_emit = sub.add_parser(
        "emit",
        help="emit a Buildroot config fragment or a report from pins.toml",
    )
    p_emit.add_argument(
        "path",
        metavar="PINS.TOML",
        nargs="?",
        default="pins.toml",
        help="path to the pins.toml to emit from (default: pins.toml)",
    )
    p_emit.add_argument(
        "--format",
        choices=ALL_FORMATS,
        default=ALL_FORMATS[0],
        help="output format (default: %(default)s)",
    )

    p_lock = sub.add_parser(
        "lock",
        help="write the resolved manifest to a lockfile (pins.lock.toml)",
    )
    p_lock.add_argument(
        "path",
        metavar="PINS.TOML",
        nargs="?",
        default="pins.toml",
        help="path to the pins.toml to lock (default: pins.toml)",
    )
    p_lock.add_argument(
        "--lock",
        default=DEFAULT_LOCKFILE,
        metavar="PATH",
        help=f"path to the lockfile to write (default: {DEFAULT_LOCKFILE})",
    )

    p_drift = sub.add_parser(
        "drift",
        help="diff pins.toml against a lockfile (added/removed/version/hash)",
    )
    p_drift.add_argument(
        "path",
        metavar="PINS.TOML",
        nargs="?",
        default="pins.toml",
        help="path to the pins.toml to diff (default: pins.toml)",
    )
    p_drift.add_argument(
        "--lock",
        default=DEFAULT_LOCKFILE,
        metavar="PATH",
        help=f"path to the lockfile to diff against (default: {DEFAULT_LOCKFILE})",
    )

    return parser


def _cmd_validate(args: argparse.Namespace) -> int:
    """Validate the pins file at ``args.path``.

    Returns ``0`` when the file is valid, ``1`` when it fails validation (or
    cannot be read/parsed), and lets argparse handle usage errors.
    """
    try:
        pins = load_pins(args.path)
    except (PinsError, tomllib.TOMLDecodeError) as exc:
        # Missing file, malformed TOML, or a schema (required-field) failure.
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    errors = validate_pins(pins)
    if errors:
        for message in errors:
            print(f"invalid: {message}", file=sys.stderr)
        return 1
    print(f"ok: {len(pins)} component(s) valid")
    return 0


def _cmd_emit(args: argparse.Namespace) -> int:
    """Emit a Buildroot fragment or report for the pins file at ``args.path``.

    The file must first pass validation (emit only produces artefacts from a
    valid manifest).  Returns ``0`` on success and ``1`` on any error.
    """
    try:
        pins = load_pins(args.path)
    except (PinsError, tomllib.TOMLDecodeError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    errors = validate_pins(pins)
    if errors:
        for message in errors:
            print(f"invalid: {message}", file=sys.stderr)
        return 1
    try:
        text = emit(pins, args.format)
    except ValueError as exc:
        # Unreachable via argparse (choices), but keep the contract tight.
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    print(text, end="")
    return 0


def _cmd_lock(args: argparse.Namespace) -> int:
    """Write the validated pins file at ``args.path`` to a lockfile.

    Only a manifest that passes validation is locked (the lockfile is the
    resolved baseline, so it must be a valid manifest).  Returns ``0`` on
    success and ``1`` on any error.
    """
    try:
        pins = load_pins(args.path)
    except (PinsError, tomllib.TOMLDecodeError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    errors = validate_pins(pins)
    if errors:
        for message in errors:
            print(f"invalid: {message}", file=sys.stderr)
        return 1
    path = write_lockfile(pins, args.lock)
    print(f"locked: {len(pins)} component(s) -> {path}")
    return 0


def _cmd_drift(args: argparse.Namespace) -> int:
    """Diff the pins file at ``args.path`` against its lockfile.

    Returns ``0`` when there is no drift and ``1`` when drift is detected (or
    the pins/lock file cannot be read/parsed).  A missing lockfile is a usage
    error (exit 1, printed as `invalid:`) — you cannot drift against a lockfile
    that does not exist yet; run `lock` first.
    """
    try:
        pins = load_pins(args.path)
    except (PinsError, tomllib.TOMLDecodeError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    errors = validate_pins(pins)
    if errors:
        for message in errors:
            print(f"invalid: {message}", file=sys.stderr)
        return 1
    try:
        locked = load_lockfile(args.lock)
    except (PinsError, tomllib.TOMLDecodeError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    entries = drift(pins, locked)
    print(format_drift(entries, source=args.path, lockfile=args.lock))
    return 1 if entries else 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        print(__version__)
        return 0

    if args.command == "validate":
        return _cmd_validate(args)

    if args.command == "emit":
        return _cmd_emit(args)

    if args.command == "lock":
        return _cmd_lock(args)

    if args.command == "drift":
        return _cmd_drift(args)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
