"""Command line interface for ``pengyos-initramfs``.

The CLI surface (subcommands and flags) is fixed here so it matches the
interface documented in ``spec.md``::

    pengyos-initramfs build --root DIR --spec initramfs.toml --out init.cpio.gz

``build`` loads the TOML spec, packs the spec'd files from the root directory
into a deterministic (sorted, uid/gid/mtime-normalized) gzip cpio archive, and
writes it to ``--out``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .builder import BuildError, build_archive
from .spec import SpecError, load_spec

# Exit codes.
EXIT_OK = 0
EXIT_USAGE = 2  # argparse errors / bad arguments (same as argparse default)
EXIT_INVALID_SPEC = 4  # args valid, but the spec file failed to parse/validate
EXIT_BUILD_FAILED = 5  # spec valid, but building the archive failed

__all__ = [
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_INVALID_SPEC",
    "EXIT_BUILD_FAILED",
    "build_parser",
    "main",
]


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for ``pengyos-initramfs``."""
    parser = argparse.ArgumentParser(
        prog="pengyos-initramfs",
        description=(
            "Build a deterministic (sorted, mtime-normalized) gzip cpio "
            "initramfs from a spec + a root directory."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser(
        "build", help="build an initramfs archive from a spec + root directory"
    )
    build.add_argument(
        "--root",
        required=True,
        type=Path,
        help="root directory whose contents get packed into the archive",
    )
    build.add_argument(
        "--spec",
        required=True,
        type=Path,
        help="TOML spec describing which binaries/files/modules/directories to include",
    )
    build.add_argument(
        "--out",
        required=True,
        type=Path,
        help="path to write the resulting .cpio.gz archive",
    )
    return parser


def _cmd_build(args: argparse.Namespace) -> int:
    """Handle the ``build`` subcommand.

    Validates the inputs, parses/validates the spec, then builds the
deterministic gzip cpio archive and writes it to ``--out``.
    """
    if not args.root.is_dir():
        print(
            f"pengyos-initramfs: root directory not found: {args.root}",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if not args.spec.is_file():
        print(
            f"pengyos-initramfs: spec file not found: {args.spec}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        spec = load_spec(args.spec)
    except SpecError as exc:
        print(f"pengyos-initramfs: invalid spec: {exc}", file=sys.stderr)
        return EXIT_INVALID_SPEC

    try:
        archive = build_archive(spec, args.root)
    except BuildError as exc:
        print(f"pengyos-initramfs: build failed: {exc}", file=sys.stderr)
        return EXIT_BUILD_FAILED

    try:
        if args.out.parent != Path(""):
            args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(archive)
    except OSError as exc:
        print(f"pengyos-initramfs: cannot write {args.out}: {exc}", file=sys.stderr)
        return EXIT_BUILD_FAILED

    count = len(set(spec.all_paths()) | {spec.init_path})
    print(
        f"pengyos-initramfs: wrote {args.out} "
        f"({count} entr{'y' if count == 1 else 'ies'}, "
        f"{len(archive)} bytes, deterministic newc cpio + gzip)",
        file=sys.stderr,
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code (also used by the console script)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        return _cmd_build(args)

    # Should be unreachable because subparsers are required; kept for safety.
    parser.error("a subcommand is required")
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover - thin wrapper for `python -m`
    raise SystemExit(main())
