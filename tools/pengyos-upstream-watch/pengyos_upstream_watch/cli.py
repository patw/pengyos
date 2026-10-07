"""Command-line interface for pengyos-upstream-watch.

Interface (see ``spec.md``)::

    pengyos-upstream-watch run --pins pins.toml --fetcher {http,mock} --out proposals.json

The argument surface is part of the public contract and is exercised directly
by ``tests/test_scaffold.py``. The ``run`` body validates its inputs, loads the
pins manifest (item 4), constructs the selected
:class:`~pengyos_upstream_watch.fetcher.Fetcher` (item 2), runs the detection
pipeline — version comparison (item 3) and proposal emission (item 4) — and
writes the resulting proposals to ``--out`` as a JSON array.

Exit codes
----------
* ``0`` — success.
* ``1`` — ran but failed (bad/missing input, an unreachable upstream, or no
  proposals could be emitted).
* ``2`` — usage error (argparse convention).
"""

from __future__ import annotations

import argparse
import sys

from . import __version__

EXIT_OK = 0
EXIT_FAIL = 1

FETCHERS = ("http", "mock")
def build_parser() -> argparse.ArgumentParser:
    """Build the top-level parser with the documented ``run`` subcommand."""
    parser = argparse.ArgumentParser(
        prog="pengyos-upstream-watch",
        description=(
            "Detect new upstream releases for PengyOS pins and emit change "
            "proposals (offline-testable)."
        ),
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the package version and exit.",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    run = sub.add_parser(
        "run",
        help="detect newer upstream releases for the pins and emit proposals.",
        description=(
            "Read the pins manifest, compare each pin against the latest "
            "upstream release (via the selected fetcher), and write proposals "
            "to --out."
        ),
    )
    run.add_argument(
        "--pins",
        required=True,
        metavar="PATH",
        help="path to the pins manifest (TOML).",
    )
    run.add_argument(
        "--fetcher",
        choices=FETCHERS,
        default="mock",
        help="which upstream fetcher to use (default: mock, no network).",
    )
    run.add_argument(
        "--out",
        required=True,
        metavar="PATH",
        help="path to write the proposals JSON document.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"{parser.prog} {__version__}")
        return EXIT_OK

    if args.command is None:
        # No subcommand: print help, succeed. Mirrors the reference tool.
        parser.print_help(sys.stderr)
        return EXIT_OK

    if args.command == "run":
        return _run(args)

    # argparse ``choices`` guarantees an unknown subcommand never reaches here,
    # but stay defensive for programmatic callers.
    parser.error(f"unknown command: {args.command!r}")
    return EXIT_FAIL  # unreachable


def _run(args: argparse.Namespace) -> int:
    """Body of the ``run`` subcommand.

    Loads the pins manifest (item 4), compares each pin against the latest
    upstream release via the selected :class:`~pengyos_upstream_watch.fetcher.Fetcher`
    (item 2), classifies the drift (item 3) and writes the resulting change
    proposals (item 4) to ``--out`` as a JSON array.

    A missing/invalid pins manifest raises :class:`~pengyos_upstream_watch.pins.PinsError`;
    an upstream that cannot be reached raises
    :class:`~pengyos_upstream_watch.fetcher.FetcherError`. Both are clean
    *ran-but-failed* (exit 1) outcomes, never a traceback.
    """
    import json
    from pathlib import Path

    from .fetcher import FetcherError, build_fetcher
    from .pins import PinsError, load_pins
    from .proposals import detect_proposals

    pins_path = Path(args.pins)
    if not pins_path.is_file():
        print(
            f"run: pins file not found: {pins_path}",
            file=sys.stderr,
        )
        return EXIT_FAIL

    try:
        manifest = load_pins(pins_path)
    except PinsError as exc:
        print(f"run: {exc}", file=sys.stderr)
        return EXIT_FAIL

    # Item 2: the fetcher is selected here; it only touches the network when
    # ``latest_version`` is called by the detection pipeline below.
    fetcher = build_fetcher(args.fetcher)

    try:
        proposals = detect_proposals(manifest.components, fetcher)
    except FetcherError as exc:
        print(
            f"run: could not detect upstream releases: {exc}",
            file=sys.stderr,
        )
        return EXIT_FAIL

    out_path = Path(args.out)
    try:
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump([proposal.to_dict() for proposal in proposals], fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        print(
            f"run: could not write proposals to {out_path}: {exc}",
            file=sys.stderr,
        )
        return EXIT_FAIL

    print(
        f"run: emitted {len(proposals)} proposal(s) for "
        f"{len(manifest)} pin(s) -> {out_path}"
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
