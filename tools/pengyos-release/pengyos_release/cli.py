"""Command-line interface for ``pengyos-release``.

Subcommands: ``publish``, ``index``, ``verify``, ``retain``, and the public
update feed's ``update-publish``, ``update-promote``, ``update-feed``. The argument
parsing, dispatch, error handling, and ``--json`` output are real and tested
here. Each handler returns a result dict (or raises); the concrete release
logic lives in :mod:`pengyos_release.publish` etc., added one task at a time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .errors import PengyOsError
from .feed import index_path, manifest_path
from .signing import load_signer


class FeatureNotImplemented(PengyOsError):
    """Raised by a scaffolded command handler whose logic is not yet built."""


def _add_publish(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("publish", help="publish a build output as a release")
    p.add_argument("--build", required=True, help="pengyos-build output directory")
    p.add_argument("--edition", required=True, choices=["server", "core", "desktop"])
    p.add_argument("--version", required=True, help="release version, e.g. 1.2.3")
    p.add_argument("--feed", required=True, help="feed root directory")
    p.add_argument("--channel", default="stable", help="channel to publish under (default: stable)")
    p.add_argument("--key", help="signing key (raw key or path to a key file); defaults to a deterministic stub")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _add_index(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("index", help="regenerate + verify the signed index and channel pointers")
    p.add_argument("--feed", required=True, help="feed root directory")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _add_verify(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("verify", help="verify a feed entry (or whole feed) is hashed + signed")
    p.add_argument("--feed", required=True, help="feed root directory")
    p.add_argument("--edition", help="edition to check (default: all)")
    p.add_argument("--version", help="version to check (default: all)")
    p.add_argument("--key", help="signing key (raw key or path to a key file); defaults to a deterministic stub")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _add_retain(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("retain", help="prune old versions per policy (dry-run by default)")
    p.add_argument("--feed", required=True, help="feed root directory")
    p.add_argument("--keep", type=int, required=True, help="number of newest versions to keep per edition")
    p.add_argument("--dry-run", action="store_true", help="report what would be pruned without deleting (the default)")
    p.add_argument("--apply", action="store_true", help="actually delete the pruned version dirs and re-sign the index")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-release",
        description="Publish a signed, versioned PengyOS release to a local update feed.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_publish(subparsers)
    _add_index(subparsers)
    _add_verify(subparsers)
    _add_retain(subparsers)
    _add_update(subparsers)
    return parser


def _add_update(subparsers: argparse._SubParsersAction) -> None:
    """The public update feed (pengyos-update reads it; see osupdate.py)."""
    p = subparsers.add_parser("update-publish", help="publish a build into the public update feed (signed)")
    p.add_argument("--build", required=True, help="Buildroot output dir (has target/ and images/payload/)")
    p.add_argument("--site", required=True, help="web root that holds update/")
    p.add_argument("--key", required=True, help="pengyos-sign private key (pengyos-key-v1)")
    p.add_argument("--channel", default="testing", choices=["stable", "testing"])
    p.add_argument("--notes", default="", help="release notes (text, or @FILE)")
    p.add_argument("--base-url", default="https://pengyos.catbee.ca/update/")
    p.add_argument("--force", action="store_true", help="overwrite an already published version")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    p = subparsers.add_parser("update-promote", help="add a published release to another channel")
    p.add_argument("--site", required=True)
    p.add_argument("--image", required=True, help="IMAGE_ID, e.g. pengyos-desktop")
    p.add_argument("--release", required=True, help="YY.MM.N")
    p.add_argument("--channel", required=True, choices=["stable", "testing"])
    p.add_argument("--base-url", default="https://pengyos.catbee.ca/update/")
    p.add_argument("--json", action="store_true")

    p = subparsers.add_parser("download-publish", help="compress, checksum + sign install images into download/")
    p.add_argument("--build", required=True, action="append", help="Buildroot output dir (repeat per edition)")
    p.add_argument("--site", required=True, help="web root that holds download/")
    p.add_argument("--key", required=True, help="pengyos-sign private key (pengyos-key-v1)")
    p.add_argument("--force", action="store_true", help="recompress images that already exist")
    p.add_argument("--json", action="store_true")

    p = subparsers.add_parser("update-feed", help="regenerate update/feed.xml + index.html")
    p.add_argument("--site", required=True)
    p.add_argument("--base-url", default="https://pengyos.catbee.ca/update/")
    p.add_argument("--json", action="store_true")


# Dispatch table: command -> handler. Handlers are added as each feature lands.
HANDLERS: dict[str, Callable[[argparse.Namespace], Any]] = {}


def register(name: str) -> Callable[[Callable], Callable]:
    def wrap(fn: Callable) -> Callable:
        HANDLERS[name] = fn
        return fn

    return wrap


@register("publish")
def _publish(args: argparse.Namespace) -> dict[str, Any]:
    from .publish import publish as publish_release

    signer = load_signer(args.key) if args.key else None
    manifest = publish_release(
        Path(args.build),
        args.edition,
        args.version,
        Path(args.feed),
        signer=signer,
        channel=args.channel,
    )
    return {
        "edition": manifest.edition,
        "version": manifest.version,
        "manifest": str(manifest_path(Path(args.feed), manifest.edition, manifest.version)),
        "channel": args.channel,
        "artifacts": [a.to_dict() for a in manifest.artifacts],
    }


@register("index")
def _index(args: argparse.Namespace) -> dict[str, Any]:
    from .feed import regenerate_index

    index = regenerate_index(Path(args.feed))
    return {
        "releases": [
            {"edition": r.edition, "version": r.version} for r in index.releases
        ],
        "channels": dict(index.channels),
        "index": str(index_path(Path(args.feed))),
        "signature": index.signature,
    }


@register("verify")
def _verify(args: argparse.Namespace) -> dict[str, Any]:
    from .feed import verify_feed
    from .signing import load_signer

    signer = load_signer(args.key) if args.key else None
    result = verify_feed(Path(args.feed), signer=signer)
    return {
        "ok": result.ok,
        "artifacts_verified": result.artifacts_verified,
        "manifests_verified": result.manifests_verified,
        "index_valid": result.index_valid,
        "problems": result.problems,
    }


@register("retain")
def _retain(args: argparse.Namespace) -> dict[str, Any]:
    from .feed import retain

    # Retain is a dry run by default; --apply actually prunes + re-signs the index.
    result = retain(Path(args.feed), keep=args.keep, dry_run=not args.apply)
    return {
        "dry_run": result.dry_run,
        "kept": result.kept,
        "pruned": result.pruned,
        "removed": [str(p) for p in result.removed],
    }


@register("update-publish")
def _update_publish(args: argparse.Namespace) -> dict[str, Any]:
    from .osupdate import publish_update, write_feed

    notes = args.notes
    if notes.startswith("@"):
        notes = Path(notes[1:]).read_text(encoding="utf-8")
    pub = publish_update(Path(args.build), Path(args.site), Path(args.key).expanduser(),
                         channel=args.channel, notes=notes, force=args.force)
    write_feed(Path(args.site), args.base_url)
    if not args.json:
        print(f"published {pub.image} {pub.version} ({args.channel}) -> {pub.path}")
    return {"image": pub.image, "version": pub.version, "path": str(pub.path), "channel": args.channel}


@register("update-promote")
def _update_promote(args: argparse.Namespace) -> dict[str, Any]:
    from .osupdate import promote, write_feed

    promote(Path(args.site), args.image, args.release, args.channel)
    write_feed(Path(args.site), args.base_url)
    if not args.json:
        print(f"{args.image} {args.release} is now on {args.channel}")
    return {"image": args.image, "version": args.release, "channel": args.channel}


@register("download-publish")
def _download_publish(args: argparse.Namespace) -> dict[str, Any]:
    from .downloads import publish_downloads

    dest = publish_downloads([Path(b) for b in args.build], Path(args.site),
                             Path(args.key).expanduser(), force=args.force)
    listing = json.loads((dest / "downloads.json").read_text())
    if not args.json:
        for e in listing["images"]:
            print(f"{e['file']}  {e['size'] >> 20} MiB")
        print(f"-> {dest}")
    return listing


@register("update-feed")
def _update_feed(args: argparse.Namespace) -> dict[str, Any]:
    from .osupdate import write_feed

    paths = write_feed(Path(args.site), args.base_url)
    if not args.json:
        print("wrote " + ", ".join(str(p) for p in paths))
    return {"written": [str(p) for p in paths]}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces a known command
        parser.error(f"unknown command {args.command!r}")
    try:
        result = handler(args)
    except FeatureNotImplemented as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except PengyOsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
