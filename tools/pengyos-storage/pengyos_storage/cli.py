"""The ``pengyos-storage`` command line.

Three subcommands (see spec.md): ``disks`` (the filesystem view), ``scan`` (the
visualizer: text, HTML or JSON) and ``du`` (the sorted ``du``).

Exit codes: ``0`` success, ``2`` bad usage or an unusable path, ``1`` an
unexpected failure. A scan that hit unreadable directories is a success: the
warnings are in the report.

``--json`` is accepted **before or after** the subcommand, like every other
state-inspecting tool in this fleet.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import SCHEMA, TOOL, VERSION
from .html import render_disks_html, render_html
from .model import ScanError, parse_size
from .report import render_disks, render_du, render_text
from .scan import list_filesystems, scan_tree

_FORMATS = ("text", "html", "json")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("paths", nargs="*", metavar="PATH", help="tree(s) to scan (default: .)")
    p.add_argument("-f", "--format", choices=_FORMATS, default="text",
                   help="output format (default: text)")
    p.add_argument("-o", "--out", metavar="FILE", help="write the document to FILE")
    p.add_argument("-d", "--depth", type=int, default=3, metavar="N",
                   help="how deep to report (default 3); deeper space is summed into the node that stops")
    p.add_argument("-n", "--top", type=int, default=15, metavar="N",
                   help="how many entries per level, and how many slices (default 15)")
    p.add_argument("--min-size", metavar="SIZE", default="0",
                   help="hide entries below this size from the report (e.g. 10M); totals stay exact")
    p.add_argument("--apparent", action="store_true",
                   help="report apparent size (st_size) instead of disk usage (st_blocks*512)")
    p.add_argument("-x", "--one-file-system", action="store_true",
                   help="do not descend into directories on other filesystems")
    p.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                   help="fnmatch pattern to skip (repeatable); skipped space is reported as excluded")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                   help="machine-readable output")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=TOOL,
        description="See where the disk went: a sorted, bar-charted, pie-charted du.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            f"  {TOOL} disks\n"
            f"  {TOOL} scan /data -f html -o storage.html\n"
            f"  {TOOL} scan ~ -d 2 --exclude '*.iso' --min-size 100M\n"
            f"  {TOOL} du /data -d 2 | head\n"
            f"  {TOOL} --json scan /data\n"
        ),
    )
    ap.add_argument("--version", action="version", version=f"{TOOL} {VERSION}")
    ap.add_argument("--json", action="store_true", default=False,
                    help="machine-readable output (also accepted after the subcommand)")
    sub = ap.add_subparsers(dest="cmd", metavar="COMMAND")

    disks = sub.add_parser("disks", help="every filesystem, with a used-space bar")
    disks.add_argument("-f", "--format", choices=_FORMATS, default="text")
    disks.add_argument("-o", "--out", metavar="FILE", help="write the document to FILE")
    disks.add_argument("--network", action="store_true",
                       help="include network filesystems (measuring a dead NFS mount can block)")
    disks.add_argument("--mounts", metavar="FILE", default="/proc/mounts",
                       help="mount table to read (default /proc/mounts)")
    disks.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    scan = sub.add_parser("scan", help="scan a tree: bars, pie + treemap HTML, or JSON")
    _add_common(scan)

    du = sub.add_parser("du", help="du, sorted biggest-first, with share bars")
    du.add_argument("paths", nargs="*", metavar="PATH", help="tree(s) to scan (default: .)")
    du.add_argument("-d", "--depth", type=int, default=1, metavar="N",
                    help="report this many levels below each path (default 1)")
    du.add_argument("-a", "--all", action="store_true", help="include individual files")
    du.add_argument("--no-bar", action="store_true", help="plain size + path, no bar")
    du.add_argument("--min-size", metavar="SIZE", default="0", help="hide entries below this size")
    du.add_argument("--apparent", action="store_true")
    du.add_argument("-x", "--one-file-system", action="store_true")
    du.add_argument("--exclude", action="append", default=[], metavar="PATTERN")
    du.add_argument("-o", "--out", metavar="FILE", help="write to FILE")
    du.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return ap


def _emit(text: str, out: str | None) -> int:
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{TOOL}: wrote {out}")
    else:
        sys.stdout.write(text)
    return 0


def _scan_kwargs(args) -> dict:
    return {
        "mode": "apparent" if args.apparent else "blocks",
        "one_file_system": args.one_file_system,
        "excludes": tuple(args.exclude),
    }


def _format(args) -> str:
    """The output format: ``--json`` wins over ``--format``, wherever it was given."""
    if getattr(args, "json", False):
        return "json"
    return getattr(args, "format", "text")


def _min_size(args) -> int:
    """``--min-size`` is a size string (``10M``); the renderers want bytes."""
    return parse_size(args.min_size)


def _documents(results):
    """One scan → one document; several → a wrapper with a ``scans`` list."""
    if len(results) == 1:
        return results[0].to_dict()
    doc = {
        "tool": TOOL,
        "version": VERSION,
        "schema": SCHEMA,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scans": [r.to_dict() for r in results],
    }
    return doc


def _do_scan(args) -> int:
    results = [scan_tree(path, **_scan_kwargs(args)) for path in (args.paths or ["."])]
    fmt = _format(args)
    min_size = _min_size(args)
    if fmt == "json":
        doc = _documents(results)
        return _emit(json.dumps(doc, indent=2) + "\n", args.out)
    if fmt == "html":
        if len(results) > 1:
            print(f"{TOOL}: --format html takes one PATH", file=sys.stderr)
            return 2
        return _emit(render_html(results[0], depth=args.depth, top=args.top,
                                  min_size=min_size), args.out)
    chunks = [
        render_text(r, depth=args.depth, top=args.top, min_size=min_size)
        for r in results
    ]
    return _emit("\n".join(chunks), args.out)


def _do_du(args) -> int:
    results = [scan_tree(path, **_scan_kwargs(args)) for path in (args.paths or ["."])]
    if _format(args) == "json":
        doc = _documents(results)
        return _emit(json.dumps(doc, indent=2) + "\n", args.out)
    chunks = [
        render_du(r, depth=args.depth, all_files=args.all, min_size=_min_size(args),
                  show_bar=not args.no_bar)
        for r in results
    ]
    return _emit("\n".join(c for c in chunks if c), args.out)


def _do_disks(args) -> int:
    filesystems = list_filesystems(args.mounts, include_network=args.network)
    if _format(args) == "json":
        doc = {
            "tool": TOOL, "version": VERSION, "schema": SCHEMA,
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "filesystems": [f.to_dict() for f in filesystems],
        }
        return _emit(json.dumps(doc, indent=2) + "\n", args.out)
    if _format(args) == "html":
        return _emit(render_disks_html(filesystems), args.out)
    return _emit(render_disks(filesystems), args.out)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 2

    handlers = {"scan": _do_scan, "du": _do_du, "disks": _do_disks}
    try:
        return handlers[args.cmd](args)
    except ScanError as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print(f"{TOOL}: interrupted", file=sys.stderr)
        return 130
    except OSError as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
