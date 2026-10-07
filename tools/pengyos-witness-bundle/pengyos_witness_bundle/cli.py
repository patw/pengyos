"""Command-line interface for pengyos-witness-bundle.

Two subcommands:

* ``layout`` — print the canonical witness artifact layout this tool produces.
* ``run``    — normalise a pengyos-build output into that layout (by copying or
  symlinking the discovered artifacts to ``<build>/.witness/``) and (optionally)
  verify the SBOM signature.

The CLI is stdlib-only (``argparse``). Exit codes:

* ``0`` — success (``run`` only when required artifacts exist and, if a key was
  given, the signature verified).
* ``2`` — usage error (bad/missing arguments).
* ``1`` — runtime error (build dir missing, signature failed, artifacts
  missing, ...).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

from . import __version__
from .bundle import bundle
from .discovery import DiscoveryResult, discover
from .layout import canonical_names
from .sigverify import VerifyError, verify_file

PROG = "pengyos-witness-bundle"


# The canonical witness layout, as a ``{kind: conventional_name}`` dict. The
# authoritative, alias-aware definition lives in :mod:`pengyos_witness_bundle.layout`
# (task 2); we keep this dict-shaped view as the stable surface ``layout`` prints
# and the tests assert on.
CANONICAL_LAYOUT: dict[str, str] = canonical_names()


def _print_layout(payload: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"# canonical witness layout produced by {PROG}\n")
        for kind, name in sorted(payload.items()):
            print(f"  {kind:<16} -> {name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Bundle a pengyos-build output into the canonical witness layout and "
            "verify its SBOM signature."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
    )

    layout = sub.add_parser(
        "layout",
        help="Print the canonical witness artifact layout.",
        description="Print the canonical witness artifact layout this tool produces.",
    )
    layout.add_argument(
        "--json",
        action="store_true",
        help="Emit the layout as JSON instead of a human table.",
    )

    run = sub.add_parser(
        "run",
        help="Normalise a build output into the witness layout (iterative).",
        description=(
            "Normalise a pengyos-build output into the canonical witness layout and "
            "verify its SBOM signature."
        ),
    )
    run.add_argument(
        "--build",
        required=True,
        metavar="DIR",
        help="Path to a pengyos-build output directory to normalise.",
    )
    run.add_argument(
        "--pub",
        metavar="KEY",
        default=None,
        help="Path to the ed25519 public key used to verify the SBOM signature.",
    )
    run.add_argument(
        "--link",
        action="store_true",
        help=(
            "Materialise the witness view with symlinks (default is to copy). "
            "The .witness/ dir is then a view of the build, not a duplicate."
        ),
    )
    run.add_argument(
        "--json",
        metavar="OUT",
        nargs="?",
        const="stdout",
        default="",
        help=(
            "Write the machine-readable result as JSON. With no argument, emit to "
            "stdout (the default when --json is given); with an argument, write to "
            "that path."
        ),
    )
    return parser


def cmd_layout(args: argparse.Namespace) -> int:
    return _print_layout(CANONICAL_LAYOUT, args.json)


def _print_run_result(result: dict) -> None:
    """Human-readable rendering of a ``run`` result."""
    status = "witness-ready" if result["witness_ready"] else "NOT witness-ready"
    print(f"# pengyos-witness-bundle run")
    print(f"  build dir : {result['build_dir']}")
    print(f"  status    : {status}")
    print(f"  complete  : {result['complete']} ({len(result['present'])}/4 present)")
    if result["present"]:
        print(f"  present   : {', '.join(result['present'])}")
    if result["missing"]:
        print(f"  missing   : {', '.join(result['missing'])}")
    sig = result["signature"]
    if sig is None:
        if result["signature_requested"]:
            # --pub was given but verification hard-failed (tamper / bad key /
            # missing input): identified on stderr, never masked.
            print("  signature : FAILED (hard fail — see stderr)")
        else:
            print("  signature : (no --pub given)")
    else:
        print(f"  signature : OK via {sig['method']}")
        print(f"              sbom      {sig['sbom']}")
        print(f"              signature {sig['signature']}")
        print(f"              public key {sig['public_key']}")
    view = result["witness_view"]
    print(f"  witness dir : {view['witness_dir']} ({view['mode']})")
    if view["bundled"]:
        for item in view["bundled"]:
            print(
                f"              {item['kind']:<14} {item['source']} -> "
                f"{item['destination']} ({item['method']})"
            )
    if view["skipped"]:
        print(f"              skipped (not present): {', '.join(view['skipped'])}")


def _verify_sbom_signature(
    build_dir: Path, discovery: DiscoveryResult, pub: str
) -> tuple[dict | None, int]:
    """Attempt SBOM signature verification when ``--pub`` was given.

    Returns ``(signature_result, error_rc)``. On success the signature result
    describes the verified signature; on any failure the signature result is
    ``None`` and ``error_rc`` is the exit code to return (``1``).
    """
    sbom = next((a for a in discovery.artifacts if a.kind == "sbom"), None)
    sig = next((a for a in discovery.artifacts if a.kind == "sbom_signature"), None)
    if sbom is None or sig is None:
        missing = "SBOM" if sbom is None else "SBOM signature"
        print(
            f"{PROG}: error: cannot verify SBOM signature: missing {missing} "
            f"(needed to verify --pub key)",
            file=sys.stderr,
        )
        return None, 1

    try:
        verified, method = verify_file(
            build_dir / sbom.path,
            build_dir / sig.path,
            pub,
        )
    except VerifyError as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return None, 1

    return (
        {
            "verified": verified,
            "method": method,
            "sbom": sbom.path,
            "signature": sig.path,
            "public_key": str(Path(pub)),
        },
        0 if verified else 1,
    )


def _bundle_artifacts(
    build_dir: Path, discovery: DiscoveryResult, link: bool
) -> tuple[dict | None, int]:
    """Materialise the canonical witness layout under ``<build>/.witness/``.

    Returns ``(view_result, error_rc)``. On success the view result describes the
    witness view (its directory, mode, and what was bundled/skipped); on any
    structural failure the view result is ``None`` and ``error_rc`` is ``1``.
    """
    mode = "symlink" if link else "copy"
    try:
        view = bundle(build_dir, discovery, mode=mode)
    except (OSError, ValueError) as exc:
        print(
            f"{PROG}: error: cannot build witness view at {build_dir / '.witness'}: "
            f"{exc}",
            file=sys.stderr,
        )
        return None, 1

    return (
        {
            "witness_dir": view.witness_dir,
            "mode": mode,
            "bundled": [
                {
                    "kind": b.kind,
                    "source": b.source,
                    "destination": b.destination,
                    "method": b.method,
                }
                for b in view.bundled
            ],
            "skipped": list(view.skipped),
        },
        0,
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Normalise a build output into the witness layout and verify its SBOM.

    Exits ``0`` only when the required artifacts exist and, if ``--pub`` was
    given, the SBOM signature verifies. Any other outcome is a clean, non-zero
    exit (a runtime error, never a traceback).
    """
    build_dir = Path(args.build)
    if not build_dir.exists():
        print(
            f"{PROG}: error: build dir not found: {args.build}",
            file=sys.stderr,
        )
        return 1

    discovery = discover(build_dir)

    result: dict = {
        "build_dir": str(build_dir),
        "witness_ready": False,
        "complete": discovery.complete,
        "present": list(discovery.present),
        "missing": list(discovery.missing),
        "signature": None,
        "signature_requested": args.pub is not None,
        "witness_view": None,
    }

    rc = 0
    if args.pub is not None:
        signature, sig_rc = _verify_sbom_signature(build_dir, discovery, args.pub)
        rc = sig_rc
        result["signature"] = signature

    # Materialise the canonical witness layout (copy or symlink the discovered
    # artifacts to <build>/.witness/). A structural failure here is a clean
    # runtime error, never a traceback.
    view, view_rc = _bundle_artifacts(build_dir, discovery, args.link)
    rc = rc or view_rc
    result["witness_view"] = view

    result["witness_ready"] = discovery.complete and rc == 0

    # ``run`` exits 0 only when the build is witness-ready (all required
    # artifacts present, and — if a key was given — the signature verified).
    rc = 0 if result["witness_ready"] else 1

    if args.json:
        payload = json.dumps(result, indent=2, sort_keys=True)
        if args.json == "stdout":
            print(payload)
        else:
            Path(args.json).write_text(payload + "\n", encoding="utf-8")
            print(f"{PROG}: wrote result to {args.json}", file=sys.stderr)
    else:
        _print_run_result(result)

    return rc


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    if args.command == "layout":
        return cmd_layout(args)
    if args.command == "run":
        try:
            return cmd_run(args)
        except NotImplementedError as exc:
            print(f"{PROG}: error: {exc}", file=sys.stderr)
            return 2
        except OSError as exc:
            print(f"{PROG}: error: {exc}", file=sys.stderr)
            return 1
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover - argparse exits before reaching here


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
