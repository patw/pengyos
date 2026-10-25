"""Command-line interface for pengyos-appimage.

Three commands, none of which executes or unpacks the AppImage:

* ``inspect``   -- what the AppImage is: type, arch, compression, update info,
                   signature, and the ``.desktop`` entry and icon it carries.
* ``integrate`` -- add it to the app launcher in place (``--remove`` undoes it).
* ``check``     -- name the libraries it needs that this system lacks.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__

# Exit codes:
#   0  success (check: nothing missing)
#   1  error (unreadable file, not an AppImage, ...)
#   2  argparse usage error
#   4  check: one or more libraries missing
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_MISSING = 4


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands registered."""
    parser = argparse.ArgumentParser(
        prog="pengyos-appimage",
        description=(
            "Inspect AppImages, add them to the app launcher, and find the "
            "libraries they need. Never runs or unpacks an AppImage."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    p = sub.add_parser(
        "inspect",
        help="show what an AppImage is, without running it",
        description="Show the AppImage's type, update info, signature, .desktop entry and icon.",
    )
    p.add_argument("appimage", help="path to the .AppImage file")
    p.add_argument("--json", action="store_true", help="emit the report as JSON")

    p = sub.add_parser(
        "integrate",
        help="add an AppImage to the app launcher (where it is)",
        description=(
            "Write a launcher entry and icon for the AppImage under the XDG data "
            "home. The entry runs the AppImage from where it is now; move it and "
            "integrate again."
        ),
    )
    p.add_argument("appimage", help="path to the .AppImage file")
    p.add_argument(
        "--remove", action="store_true",
        help="remove the entry and icon written for this path instead",
    )
    p.add_argument(
        "--dest", default=None, metavar="DIR",
        help="XDG data home to write under (default: $XDG_DATA_HOME or ~/.local/share)",
    )
    p.add_argument("--json", action="store_true", help="emit the report as JSON")

    p = sub.add_parser(
        "check",
        help="name the libraries an AppImage needs that this system lacks",
        description=(
            "Read every ELF object in the payload and report the libraries "
            "neither the AppImage nor this system provides. Exit status 4 when "
            "something is missing."
        ),
    )
    p.add_argument("appimage", help="path to the .AppImage file")
    p.add_argument(
        "--libdirs", nargs="+", metavar="DIR", default=None,
        help="host library directories (default: the loader's, from /etc/ld.so.conf)",
    )
    p.add_argument("--json", action="store_true", help="emit the report as JSON")
    return parser


def _cmd_inspect(args: argparse.Namespace) -> int:
    from . import inspect as inspector

    try:
        r = inspector.inspect_appimage(args.appimage)
    except inspector.InspectionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(r.to_dict(), indent=2, sort_keys=True))
        return EXIT_OK
    print(f"Type:     {r.type}")
    print(f"Arch:     {r.arch}")
    print(f"Payload:  squashfs ({r.compression}) at offset {r.payload_offset}")
    print(f"Update:   {r.update_info or '(none)'}")
    print(f"Signed:   {'yes' if r.signed else 'no'}")
    print("Desktop:")
    print(f"  Path: {r.desktop.path}")
    print(f"  Name: {r.desktop.name}")
    print(f"  Icon: {r.desktop.icon}")
    print(f"  Exec: {r.desktop.exec}")
    print(f"  Type: {r.desktop.type}")
    if r.icon.found:
        print(f"Icon:     {r.icon.path} ({r.icon.size} bytes)")
    else:
        print("Icon:     not found")
    return EXIT_OK


def _cmd_integrate(args: argparse.Namespace) -> int:
    from . import integrate as integrator

    if args.remove:
        removed = integrator.remove_integration(args.appimage, args.dest)
        if args.json:
            print(json.dumps({"removed": [str(p) for p in removed]}, indent=2))
        elif removed:
            for p in removed:
                print(f"Removed {p}")
        else:
            print(f"Nothing was integrated for {args.appimage}")
        return EXIT_OK

    try:
        r = integrator.integrate_appimage(args.appimage, args.dest)
    except integrator.IntegrateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(r.to_dict(), indent=2, sort_keys=True))
        return EXIT_OK
    print(f"{'Replaced' if r.replaced else 'Added'} {r.desktop_path}")
    print(f"Icon:  {r.icon_path or '(none installed)'}")
    for warning in r.warnings:
        print(f"warning: {warning}")
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    from . import check as checker

    try:
        r = checker.check_appimage(args.appimage, args.libdirs)
    except checker.CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps(r.to_dict(), indent=2, sort_keys=True))
        return EXIT_OK if r.ok else EXIT_MISSING
    foreign = f", {r.skipped_foreign} for other architectures skipped" if r.skipped_foreign else ""
    print(f"Read {r.elf_count} {r.arch} ELF objects{foreign}.")
    for path in r.unreadable:
        print(f"warning: could not parse {path}")
    if r.ok:
        print("Every library they need is in the AppImage or on this system.")
        return EXIT_OK
    print(f"Missing {len(r.missing)}:")
    for m in r.missing:
        how = "loaded at run time by" if m.dlopen else "needed by"
        print(f"  {m.soname}")
        print(f"    {how}: {', '.join(m.needed_by)}")
        print(f"    {m.hint}")
    return EXIT_MISSING


_HANDLERS = {
    "inspect": _cmd_inspect,
    "integrate": _cmd_integrate,
    "check": _cmd_check,
}


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
