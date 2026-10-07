"""Command-line interface for pengyos-cosmic-pkg.

A thin argparse front-end that registers the three documented subcommands
(``vendor``, ``recipe``, ``lint``) and delegates each to its focused feature
module. The subcommand surface (names, arguments, JSON mode, exit codes) is the
public contract; the feature bodies live in :mod:`pengyos_cosmic_pkg.vendor`,
:mod:`pengyos_cosmic_pkg.recipe` and :mod:`pengyos_cosmic_pkg.lint`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .manifest import ManifestError

PROG = "pengyos-cosmic-pkg"


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
            "Turn a git Rust component (URL + pinned tag + Cargo.lock) into a "
            "Buildroot cargo package with a vendored, git-dependency-aware "
            "source tree."
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

    # vendor — deterministic vendored source tree from a component manifest.
    vendor_parser = subparsers.add_parser(
        "vendor",
        help="vendor a component's source (rewriting git deps to the vendored path)",
        description=(
            "Read a component manifest + Cargo.lock and produce a deterministic "
            "vendored source tree under --out, rewriting git sources to the "
            "vendored path and emitting the source .hash."
        ),
    )
    vendor_parser.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="path to the component manifest (TOML)",
    )
    vendor_parser.add_argument(
        "--cargo-lock",
        required=True,
        metavar="PATH",
        help="path to the workspace Cargo.lock (pins the git deps to vendor)",
    )
    vendor_parser.add_argument(
        "--git-sources",
        metavar="DIR",
        default=None,
        help=(
            "local mirror of the checked-out git crates, keyed by repo slug "
            "(defaults to the --cargo-lock dir/git-sources)"
        ),
    )
    vendor_parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="directory to write the vendored source tree into",
    )
    _add_common_json_flag(vendor_parser)
    vendor_parser.set_defaults(func=_cmd_vendor)

    # recipe — emit a Buildroot cargo package.
    recipe_parser = subparsers.add_parser(
        "recipe",
        help="emit a Buildroot cargo package (Config.in / .mk / .hash)",
        description=(
            "Emit package/<pkg>/{Config.in,<pkg>.mk,<pkg>.hash} in Buildroot "
            "cargo form, with version and license pinned."
        ),
    )
    recipe_parser.add_argument(
        "--manifest",
        required=True,
        metavar="PATH",
        help="path to the component manifest (TOML)",
    )
    recipe_parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="Buildroot package/ directory to emit the recipe into",
    )
    recipe_parser.add_argument(
        "--hash-file",
        required=True,
        metavar="PATH",
        help=(
            "Buildroot .hash file carrying the sha256 of the vendored source "
            "(the <pkg>.hash the vendor subcommand emits); its digest is pinned "
            " into the emitted recipe"
        ),
    )
    recipe_parser.add_argument(
        "--site",
        metavar="URL",
        default=None,
        help=(
            "value for *_SITE (where Buildroot finds the vendored tarball); "
            "defaults to the placeholder file://<VENDORED_SITE>"
        ),
    )
    _add_common_json_flag(recipe_parser)
    recipe_parser.set_defaults(func=_cmd_recipe)

    # lint — validate a Buildroot recipe tree.
    lint_parser = subparsers.add_parser(
        "lint",
        help="lint a Buildroot recipe tree (hash/version/license/floats)",
        description=(
            "Lint a Buildroot package directory: require a present .hash, an "
            "explicitly pinned version, a declared license, and no floating "
            "git branch/ref sources."
        ),
    )
    lint_parser.add_argument(
        "--dir",
        required=True,
        metavar="DIR",
        help="Buildroot package/ directory to lint",
    )
    _add_common_json_flag(lint_parser)
    lint_parser.set_defaults(func=_cmd_lint)

    return parser


def _cmd_vendor(args: argparse.Namespace) -> int:
    from .vendor import main as vendor_main

    return vendor_main(args)


def _cmd_recipe(args: argparse.Namespace) -> int:
    from .recipe import main as recipe_main

    return recipe_main(args)


def _cmd_lint(args: argparse.Namespace) -> int:
    from .lint import main as lint_main

    return lint_main(args)


def _validate_inputs(args: argparse.Namespace) -> None:
    """Validate that a subcommand's required input path exists on disk.

    This is framework-level input validation: the CLI checks that the manifest
    (``vendor``/``recipe``) or package directory (``lint``) is present before
    delegating. Semantic validity of the manifest is the feature's job.
    """
    command = args.command
    if command in ("vendor", "recipe"):
        _require_file(args.manifest)
    if command == "recipe":
        # The recipe pins the vendored source's digest from a Buildroot .hash
        # file (produced by the vendor subcommand); it must be present.
        _require_file(args.hash_file)
    elif command == "lint":
        _require_dir(args.dir)


def _require_file(path: str) -> None:
    from pathlib import Path

    if not Path(path).is_file():
        raise FileNotFoundError(path)


def _require_dir(path: str) -> None:
    from pathlib import Path

    if not Path(path).is_dir():
        raise FileNotFoundError(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    # Framework-level input validation (missing manifest / package dir).
    try:
        _validate_inputs(args)
        return int(args.func(args))
    except FeatureNotReady as exc:
        # A subcommand whose feature body is not yet implemented: report cleanly.
        if getattr(args, "json", False):
            import json

            print(
                json.dumps(
                    {
                        "command": args.command,
                        "ok": False,
                        "error": str(exc),
                    }
                )
            )
        else:
            print(f"{PROG}: {args.command}: {exc}", file=sys.stderr)
        return 1
    except ManifestError as exc:
        # The feature module rejected its input (bad manifest / Cargo.lock /
        # missing git source): report cleanly, never as a traceback.
        if getattr(args, "json", False):
            import json

            print(
                json.dumps(
                    {
                        "command": args.command,
                        "ok": False,
                        "error": str(exc),
                    }
                )
            )
        else:
            print(f"{PROG}: {args.command}: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        # The feature module rejected its input content (e.g. a malformed
        # recipe hash file): report cleanly, never as a traceback.
        if getattr(args, "json", False):
            import json

            print(
                json.dumps(
                    {
                        "command": args.command,
                        "ok": False,
                        "error": str(exc),
                    }
                )
            )
        else:
            print(f"{PROG}: {args.command}: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        # A required input path (manifest / hash file / package dir) is missing:
        # report cleanly, never as a traceback. ``str(exc)`` carries the path.
        message = exc.filename if exc.filename is not None else str(exc)
        if getattr(args, "json", False):
            import json

            print(
                json.dumps(
                    {
                        "command": args.command,
                        "ok": False,
                        "error": f"file not found: {message}",
                    }
                )
            )
        else:
            print(f"{PROG}: file not found: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
