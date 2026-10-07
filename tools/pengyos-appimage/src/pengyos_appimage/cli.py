"""Command-line interface for pengyos-appimage.

This CLI intentionally never executes an AppImage's embedded hooks. It only:

* ``inspect``  -- read the AppImage type and extract the ``.desktop`` + icon
                  WITHOUT executing it (squashfs/offset parsing or
                  ``--appimage-extract`` into a temp dir).
* ``extract``  -- extract the AppImage to a prefix and validate its structure.
* ``integrate``-- generate a ``.desktop`` file, icon, and mime entries.
* ``run``      -- launch the extracted application under a bubblewrap sandbox.

All four subcommand handlers are wired here so the parser is real and
dispatchable. Handlers that are not yet implemented raise
:class:`NotYetImplementedError` (which the CLI turns into an exit code of
``3``). This is an honest placeholder, not a stub that pretends to work.
"""

from __future__ import annotations

import argparse
import sys
from typing import cast

from . import __version__

# Exit codes:
#   0  success (or help printed with no subcommand)
#   1  usage / unexpected error
#   2  argparse usage error
#   3  feature not yet implemented

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_IMPLEMENTED = 3


class NotYetImplementedError(RuntimeError):
    """Raised by a subcommand whose implementation has not been written yet."""


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered."""
    parser = argparse.ArgumentParser(
        prog="pengyos-appimage",
        description=(
            "Inspect, extract, integrate, and sandbox AppImages under PengyOS. "
            "Never executes an AppImage's embedded hooks."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(
        dest="command", metavar="<command>", required=True
    )

    # inspect: read type + .desktop + icon WITHOUT executing the AppImage.
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="read the AppImage type and extract the .desktop + icon without executing it",
        description=(
            "Read the AppImage type and extract the .desktop + icon WITHOUT "
            "executing it."
        ),
    )
    inspect_parser.add_argument(
        "appimage", help="path to the .AppImage file to inspect"
    )
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the inspection report as JSON",
    )

    # extract: extract to a prefix and validate structure.
    extract_parser = subparsers.add_parser(
        "extract",
        help="extract the AppImage to a prefix and validate structure",
        description="Extract the AppImage to a prefix and validate its structure.",
    )
    extract_parser.add_argument("appimage", help="path to the .AppImage file")
    extract_parser.add_argument(
        "--to", required=True, metavar="DIR", help="destination prefix directory"
    )

    # integrate: generate .desktop + icon + mime entries.
    integrate_parser = subparsers.add_parser(
        "integrate",
        help="generate a .desktop file, icon, and mime entries",
        description="Generate a .desktop file, icon, and mime entries for an extracted app.",
    )
    integrate_parser.add_argument(
        "app_dir", help="path to the extracted application directory"
    )
    integrate_parser.add_argument(
        "--dest",
        default=None,
        metavar="DIR",
        help=(
            "directory to write integration entries into "
            "(defaults to $XDG_DATA_HOME/applications)"
        ),
    )

    # run: launch under a bubblewrap sandbox.
    run_parser = subparsers.add_parser(
        "run",
        help="run the extracted application under a bubblewrap sandbox",
        description="Run the extracted application under a bubblewrap sandbox.",
    )
    run_parser.add_argument(
        "app_dir", help="path to the extracted application directory"
    )
    run_parser.add_argument(
        "--sandbox",
        action="store_true",
        help="enforce the bubblewrap sandbox (the default; see --no-sandbox to relax)",
    )
    run_parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="run WITHOUT the bubblewrap sandbox (insecure; prefer --sandbox)",
    )
    run_parser.add_argument(
        "--json",
        action="store_true",
        help="emit the run report as JSON",
    )

    return parser


def _cmd_inspect(args: argparse.Namespace) -> int:
    """Implement the ``inspect`` subcommand."""
    import json

    from . import inspect as inspector

    try:
        result = inspector.inspect_appimage(args.appimage)
    except inspector.InspectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        _print_inspection_report(result)
    return EXIT_OK


def _print_inspection_report(result: "InspectResult") -> None:
    """Render an inspection report as human-readable text."""
    from .inspect import InspectResult

    r = cast("InspectResult", result)
    print(f"Format:   {r.format}")
    print(f"Type:     {r.type}")
    print(f"Arch:     {r.arch}")
    print(f"Payload:  offset {r.payload_offset}")
    print("Desktop:")
    print(f"  Path: {r.desktop.path}")
    print(f"  Name: {r.desktop.name}")
    print(f"  Icon: {r.desktop.icon}")
    print(f"  Exec: {r.desktop.exec}")
    print(f"  Type: {r.desktop.type}")
    if r.icon.found:
        print(f"Icon:     found {r.icon.path} ({r.icon.size} bytes)")
    else:
        print("Icon:     not found")


def _cmd_extract(args: argparse.Namespace) -> int:
    """Implement the ``extract`` subcommand."""
    from . import extract as extractor

    try:
        result = extractor.extract_appimage(args.appimage, args.to)
    except extractor.ExtractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"Extracted to {result.prefix}")
    print(f"App entry:  {result.app_entry or '(none)'}")
    print(f"Desktop:    {result.desktop or '(none)'}")
    print(f"Root dir:   {result.root_directory or '(unspecified)'}")
    print(f"Files:      {len(result.files)} extracted")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    else:
        print("Structure OK.")
    return EXIT_OK


def _cmd_integrate(args: argparse.Namespace) -> int:
    """Implement the ``integrate`` subcommand."""
    from . import integrate as integrator

    try:
        result = integrator.integrate_app(args.app_dir, args.dest)
    except integrator.IntegrateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"Integrated {result.app_dir}")
    print(f"Destination: {result.dest}")
    print(f"Desktop:     {result.desktop_path or '(none)'}")
    print(f"Icon:        {result.icon_path or '(skipped)'}")
    if result.mime_xml_path:
        print(f"MIME:        {result.mime_xml_path} ({', '.join(result.mime_types)})")
    else:
        print("MIME:        (none needed)")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    """Implement the ``run`` subcommand."""
    from . import run as runner

    sandbox = not args.no_sandbox
    if args.sandbox and args.no_sandbox:
        print(
            "error: --sandbox and --no-sandbox are mutually exclusive",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        result = runner.run_app(
            args.app_dir,
            sandbox=sandbox,
        )
    except runner.RunError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        import json

        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
        return EXIT_OK

    print(f"Program:    {result.program or '(none)'}")
    print(f"Binary:     {result.binary or '(none)'}")
    print(f"Sandbox:    {'bubblewrap' if result.sandboxed else 'disabled (--no-sandbox)'}")
    print(f"Return:     {result.returncode if result.returncode is not None else '(not run)'}")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    return EXIT_OK


# Map subcommand -> handler. Kept as a table so dispatch is explicit and
# testable (a missing mapping is a real wiring bug, not a silent no-op).
_HANDLERS: dict[str, callable] = {
    "inspect": _cmd_inspect,
    "extract": _cmd_extract,
    "integrate": _cmd_integrate,
    "run": _cmd_run,
}


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = _HANDLERS[args.command]
    try:
        return handler(args)
    except NotYetImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    except Exception as exc:  # pragma: no cover - defensive catch-all
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
