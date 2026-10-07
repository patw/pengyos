"""Command-line interface for pengyos-cosmic-build.

A thin argparse front-end that registers the four documented subcommands
(``vendor-all``, ``emit``, ``wire``, ``lint``) and delegates each to its focused
feature module. The subcommand surface (names, arguments, JSON mode, exit codes)
is the public contract; the feature bodies live in :mod:`pengyos_cosmic_build.vendor_all`,
:mod:`pengyos_cosmic_build.emit`, :mod:`pengyos_cosmic_build.wire` and
:mod:`pengyos_cosmic_build.lint`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .emit import EmitError
from .vendor_all import VendorAllError

PROG = "pengyos-cosmic-build"


def _manifest_error_cls():
    """The manifest error the vendor-all/emit engines raise.

    Imported lazily: the loader lives in the cosmic-recipes lane (on sys.path
    only under pytest / an installed user), so importing it at module load would
    break ``python -m pengyos_cosmic_build`` on a bare checkout. The ValueError
    base class is caught directly in :func:`main`; this returns the concrete
    type for the (rare) case a caller wants it explicitly.
    """
    from pengyos_cosmic_recipes.manifest import ManifestError

    return ManifestError


class FeatureNotReady(RuntimeError):
    """A subcommand's feature body is not implemented in this iteration.

    Raised by the feature modules until their subtask lands. The CLI turns this
    into a clean, non-zero exit code with a helpful message (never a traceback),
    so an unimplemented subcommand is a *reported* condition, not a crash.
    """


def _add_common_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON summary on stdout",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Vendored, buildable COSMIC recipes + Desktop target wiring: "
            "vendor per component, emit pinned recipes, wire a BR2_EXTERNAL "
            "tree, and lint it buildable-shaped."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<command>",
        required=True,
    )

    # vendor-all — drive the vendoring engine per component.
    vendor_parser = subparsers.add_parser(
        "vendor-all",
        help="vendor every component's source (tarball + sha256 + SITE)",
        description=(
            "For each component in the manifest: locate the checked-out "
            "sources + its Cargo.lock, run the pkg vendor engine with the "
            "git-source mirror, capture the tarball, its sha256 and a real "
            "SITE, and write a vendor record per component under --out."
        ),
    )
    vendor_parser.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="path to the component manifest (TOML)",
    )
    vendor_parser.add_argument(
        "--sources",
        required=True,
        metavar="DIR",
        help="directory of checked-out component sources (one subdir per component)",
    )
    vendor_parser.add_argument(
        "--dl",
        required=True,
        metavar="DIR",
        help="download dir the vendored tarballs are written into",
    )
    vendor_parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="directory to write the per-component vendor records into",
    )
    _add_common_json_flag(vendor_parser)
    vendor_parser.set_defaults(func=_cmd_vendor_all)

    # emit — emit recipes pinning the real digest + SITE.
    emit_parser = subparsers.add_parser(
        "emit",
        help="emit Buildroot recipes pinning the real digest + SITE",
        description=(
            "Emit package/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash} pinning the "
            "real vendored digest + SITE, with the defconfig's symbol prefix."
        ),
    )
    emit_parser.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="path to the component manifest (TOML)",
    )
    emit_parser.add_argument(
        "--vendor",
        required=True,
        metavar="DIR",
        help="directory of per-component vendor records produced by vendor-all",
    )
    emit_parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Buildroot package/ directory to emit the recipes into",
    )
    emit_parser.add_argument(
        "--group",
        required=True,
        choices=["core", "login", "shell", "apps"],
        help="the group of components to emit",
    )
    _add_common_json_flag(emit_parser)
    emit_parser.set_defaults(func=_cmd_emit)

    # wire — install recipes + defconfig + board overlay + native deps + target.
    wire_parser = subparsers.add_parser(
        "wire",
        help="wire the BR2_EXTERNAL tree and register the desktop target",
        description=(
            "Install the emitted recipes + the desktop defconfig + board "
            "overlay + the sibling native-dep recipes into a BR2_EXTERNAL "
            "tree, wire Config.in, and register the pengyos-desktop target "
            "idempotently."
        ),
    )
    wire_parser.add_argument(
        "--repo",
        required=True,
        metavar="DIR",
        help="the BR2_EXTERNAL tree root to wire into",
    )
    wire_parser.add_argument(
        "--sources",
        required=True,
        metavar="DIR",
        help="the emitted-recipes source tree (package/ defconfig/ board/)",
    )
    wire_parser.add_argument(
        "--targets",
        default=None,
        metavar="PATH",
        help=(
            "the targets.toml to register the pengyos-desktop target in; omit "
            "(or pass an empty string) to wire the tree WITHOUT registering a "
            "target"
        ),
    )
    wire_parser.add_argument(
        "--defconfig",
        default="pengyos_desktop_defconfig",
        metavar="NAME",
        help="the defconfig the pengyos-desktop target resolves to",
    )
    _add_common_json_flag(wire_parser)
    wire_parser.set_defaults(func=_cmd_wire)

    # lint — assert the tree is buildable-shaped.
    lint_parser = subparsers.add_parser(
        "lint",
        help="lint the BR2_EXTERNAL tree (buildable-shaped)",
        description=(
            "Lint a BR2_EXTERNAL tree: no leftover placeholder SITE/hash, "
            "every BR2_PACKAGE_* the defconfig references resolves to a "
            "recipe, the native deps are present, and the target is "
            "registered."
        ),
    )
    lint_parser.add_argument(
        "--repo",
        required=True,
        metavar="DIR",
        help="the BR2_EXTERNAL tree root to lint",
    )
    _add_common_json_flag(lint_parser)
    lint_parser.set_defaults(func=_cmd_lint)

    return parser


def _cmd_vendor_all(args: argparse.Namespace) -> int:
    from .vendor_all import main as vendor_main

    return vendor_main(args)


def _cmd_emit(args: argparse.Namespace) -> int:
    from .emit import main as emit_main

    return emit_main(args)


def _cmd_wire(args: argparse.Namespace) -> int:
    from .wire import main as wire_main

    return wire_main(args)


def _cmd_lint(args: argparse.Namespace) -> int:
    from .lint import main as lint_main

    return lint_main(args)


def _validate_inputs(args: argparse.Namespace) -> None:
    """Validate that a subcommand's required input path exists on disk.

    This is framework-level input validation: the CLI checks that the required
    inputs are present before delegating. Semantic validity of the inputs is the
    feature's job.
    """
    command = args.command
    if command in ("vendor-all", "emit"):
        # The manifest is the one input that must pre-exist; the engine creates
        # the output dirs (--dl/--out/--vendor/--repo are created, not read).
        _require_file(args.manifest)
    if command == "emit":
        _require_dir(args.vendor)
    elif command == "wire":
        # wire reads the emitted-recipes source tree (package/ defconfig/ board/)
        # and the BR2_EXTERNAL tree (--repo); both must pre-exist.
        _require_dir(args.sources)
        _require_dir(args.repo)
    elif command == "lint":
        _require_dir(args.repo)


def _require_file(path: str) -> None:
    from pathlib import Path

    if not Path(path).is_file():
        raise FileNotFoundError(path)


def _require_dir(path: str) -> None:
    from pathlib import Path

    if not Path(path).is_dir():
        raise FileNotFoundError(path)


def _report_error(args: argparse.Namespace, message: str) -> int:
    """Report a clean, non-zero error (human text or the JSON error envelope).

    A uniform exit-1 path for every *reported* condition (FeatureNotReady,
    missing input, domain error): the message goes to stderr (human summaries
    stay clean) or the `{command, ok:false, error}` JSON envelope with --json,
    so a consumer can dispatch on ``ok`` alone. Never a traceback.
    """
    if getattr(args, "json", False):
        import json

        print(json.dumps({"command": args.command, "ok": False, "error": message}))
    else:
        print(f"{PROG}: {args.command}: {message}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    # Framework-level input validation (missing input paths).
    try:
        _validate_inputs(args)
        return int(args.func(args))
    except FeatureNotReady as exc:
        # A subcommand whose feature body is not yet implemented: report cleanly.
        return _report_error(args, str(exc))
    except FileNotFoundError as exc:
        # A required input path is missing: report cleanly, never as a traceback.
        message = exc.filename if exc.filename is not None else str(exc)
        return _report_error(args, f"file not found: {message}")
    except (ValueError, VendorAllError) as exc:
        # A component failed to vendor (missing Cargo.lock), the manifest is
        # malformed, or a vendor record is malformed: report cleanly, never as a
        # traceback. ManifestError/VendorRecordError are ValueError subclasses;
        # VendorAllError covers the engine runner. These are *reported*
        # conditions, not crashes.
        return _report_error(args, str(exc))
    except EmitError as exc:
        # A component could not be emitted (no vendor record, or a malformed /
        # placeholder record): report cleanly, never as a traceback. EmitError
        # covers the emit engine's reported conditions, not crashes.
        return _report_error(args, str(exc))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
