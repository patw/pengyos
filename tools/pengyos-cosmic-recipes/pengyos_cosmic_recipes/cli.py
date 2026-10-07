"""Command-line interface for pengyos-cosmic-recipes.

A thin argparse front-end over the recipes lane. The documented subcommands are
``manifest`` (list the COSMIC component manifest, grouped), ``emit`` (drive the
engine to emit a Buildroot cargo package per component in a group) and ``lint``
(the closure linter). All three are implemented: ``manifest`` lists the
manifest, ``emit`` emits the recipes for a group, and ``lint`` closes the
dependency loop.

``non_cargo`` handles the components the task list names but that are not cargo
components (currently ``cosmic-workspaces``, a GNOME Shell extension): ``list``
(default) lists them and their separate handling path, and ``emit`` authors the
non-cargo Buildroot recipe for ``cosmic-workspaces`` into ``--out``.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__, GROUPS, MANIFEST_NAME, load_manifest

PROG = "pengyos-cosmic-recipes"


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
            "Manifest-driven Buildroot cargo recipes for the COSMIC "
            "components, over the pengyos-cosmic-pkg engine."
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

    # manifest — list the COSMIC component manifest, grouped.
    manifest_parser = subparsers.add_parser(
        "manifest",
        help="list the COSMIC component manifest, grouped",
        description=(
            f"Load {MANIFEST_NAME} and list its components, grouped "
            f"({', '.join(GROUPS)}). Components without a group are listed "
            "under '(ungrouped)'."
        ),
    )
    manifest_parser.add_argument(
        "--group",
        choices=GROUPS,
        default=None,
        help="only list components in this group (default: all groups)",
    )
    manifest_parser.add_argument(
        "--path",
        metavar="PATH",
        default=None,
        help=f"path to the manifest (default: ./{MANIFEST_NAME})",
    )
    _add_common_json_flag(manifest_parser)
    manifest_parser.set_defaults(func=_cmd_manifest)

    # emit — drive the engine to emit a Buildroot cargo package per component.
    emit_parser = subparsers.add_parser(
        "emit",
        help="emit a Buildroot cargo package per component in a group",
        description=(
            "Emit the Buildroot cargo recipe (Config.in / <pkg>.mk / <pkg>.hash) "
            "for every component in a group, via the pengyos-cosmic-pkg engine."
        ),
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
        choices=GROUPS,
        help="the group of components to emit",
    )
    emit_parser.add_argument(
        "--hash",
        metavar="HEX",
        default=None,
        help=(
            "sha256 digest to pin into every recipe (the vendored source digest) "
            "(default: the placeholder digest, flagged in the report)"
        ),
    )
    emit_parser.add_argument(
        "--site",
        metavar="URL",
        default=None,
        help="override *_SITE (default: the engine's vendored-site placeholder)",
    )
    emit_parser.add_argument(
        "--path",
        metavar="PATH",
        default=None,
        help=f"path to the manifest (default: ./{MANIFEST_NAME})",
    )
    _add_common_json_flag(emit_parser)
    emit_parser.set_defaults(func=_cmd_emit)

    # lint — the closure linter.
    lint_parser = subparsers.add_parser(
        "lint",
        help="closure-lint the recipe tree (every component has a recipe)",
        description=(
            "Close the dependency loop: assert every component and every "
            "inter-component dependency has a lint-clean Buildroot recipe."
        ),
    )
    lint_parser.add_argument(
        "--manifest",
        metavar="PATH",
        default=None,
        help=f"path to the manifest (default: ./{MANIFEST_NAME})",
    )
    lint_parser.add_argument(
        "--dir",
        required=True,
        metavar="DIR",
        help="Buildroot package/ directory to lint",
    )
    _add_common_json_flag(lint_parser)
    lint_parser.set_defaults(func=_cmd_lint)

    # non_cargo — list the non-cargo components and their separate handling path,
    # or author the non-cargo Buildroot recipe for cosmic-workspaces.
    non_cargo_parser = subparsers.add_parser(
        "non_cargo",
        help="list the non-cargo components / author cosmic-workspaces' recipe",
        description=(
            "List the non-cargo components the recipes lane recognises (e.g. "
            "cosmic-workspaces, a GNOME Shell extension) and the separate "
            "handling path each follows: a non-cargo Buildroot recipe in a "
            "different lane, out of scope for the cargo recipes lane. With "
            "action 'emit', author cosmic-workspaces' recipe instead."
        ),
    )
    non_cargo_parser.add_argument(
        "action",
        nargs="?",
        choices=["list", "emit"],
        default="list",
        help=(
            "what to do: 'list' (default) lists the non-cargo components and "
            "their handling path; 'emit' authors the non-cargo Buildroot "
            "recipe for cosmic-workspaces into --out"
        ),
    )
    non_cargo_parser.add_argument(
        "--out",
        metavar="DIR",
        default=None,
        help=(
            "Buildroot package/ directory to emit cosmic-workspaces' recipe "
            "into (required for action 'emit')"
        ),
    )
    non_cargo_parser.add_argument(
        "--hash",
        metavar="HEX",
        default=None,
        help=(
            "sha256 digest to pin into the recipe (the vendored source digest) "
            "(default: the placeholder digest, flagged in the output)"
        ),
    )
    non_cargo_parser.add_argument(
        "--site",
        metavar="URL",
        default=None,
        help="override *_SITE (default: the vendored-site placeholder)",
    )
    non_cargo_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON summary on stdout",
    )
    non_cargo_parser.set_defaults(func=_cmd_non_cargo)

    return parser


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _cmd_manifest(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.path)
    groups = manifest.groups if args.group is None else {args.group: manifest.groups.get(args.group, ())}

    if getattr(args, "json", False):
        payload = {
            "command": "manifest",
            "ok": True,
            "path": str(manifest.path),
            "components": [
                {
                    "name": c.name,
                    "group": c.group,
                    "git_url": c.git_url,
                    "tag": c.tag,
                    "license": c.license,
                    "features": list(c.features),
                    "binaries": list(c.binaries),
                    "deps": list(c.deps),
                }
                for c in manifest.components
                if args.group is None or c.group == args.group
            ],
        }
        _emit(payload)
        return 0

    # Human output: grouped, aligned columns.
    width = max((len(name) for group in groups.values() for name in group), default=len("NAME"))
    for group in GROUPS:
        names = groups.get(group)
        if not names:
            continue
        print(f"# {group}")
        for name in names:
            entry = next(c for c in manifest.components if c.name == name)
            print(f"  {entry.name:<{width}}  {entry.tag}  {entry.license}")
            if entry.deps:
                print(f"{'':<{width}}  deps: {', '.join(entry.deps)}")
    return 0


def _cmd_emit(args: argparse.Namespace) -> int:
    """Emit the recipes for a group and report the outcome."""
    from . import emit as emit_lane

    report = emit_lane.emit_group(
        load_manifest(args.path),
        args.group,
        out_dir=args.out,
        source_hash=args.hash,
        site=args.site,
    )

    if getattr(args, "json", False):
        payload = {
            "command": "emit",
            "ok": True,
            "group": report.group,
            "out": str(report.out_dir),
            "emitted": list(report.emitted_names()),
            "missing_digest": list(report.missing_digest),
            "recipes": [
                {
                    "name": r.name,
                    "symbol": r.symbol,
                    "version": r.version,
                    "placeholder_hash": r.placeholder_hash,
                    "files": [str(p) for p in r.files],
                }
                for r in report.results
            ],
        }
        _emit(payload)
        return 0

    print(f"Emitted {len(report.results)} recipe(s) into {report.out_dir}:")
    for result in report.results:
        flag = " (placeholder hash)" if result.placeholder_hash else ""
        print(f"  - {result.symbol}{flag}")
        for path in result.files:
            print(f"      {path}")
    if report.missing_digest:
        print(
            f"{PROG}: note: {', '.join(report.missing_digest)} "
            "left on the placeholder hash (supply --hash with the vendored digest)",
            file=sys.stderr,
        )
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    """Close the dependency loop with the closure linter."""
    from . import lint as lint_lane

    manifest = load_manifest(args.manifest)
    report = lint_lane.closure_lint(manifest, args.dir)
    return lint_lane._emit(args, report)


def _cmd_non_cargo(args: argparse.Namespace) -> int:
    """List the non-cargo components, or author cosmic-workspaces' recipe.

    ``action`` is ``list`` (default) or ``emit``. ``emit`` authors the
    non-cargo Buildroot recipe for ``cosmic-workspaces`` (a GNOME shell
    extension) into ``--out`` via :mod:`pengyos_cosmic_recipes.non_cargo_recipe`.
    """
    from . import non_cargo, non_cargo_recipe

    if args.action == "emit":
        return non_cargo_recipe._emit(args)

    if getattr(args, "json", False):
        payload = {
            "command": "non_cargo",
            "ok": True,
            "components": [
                {
                    "name": c.name,
                    "git_url": c.git_url,
                    "recipe_kind": c.recipe_kind,
                    "needs_recipe": c.needs_recipe,
                    "handled_by": c.handled_by,
                    "lane_out_of_scope": c.lane_out_of_scope,
                    "note": c.note,
                }
                for c in non_cargo.NON_CARGO_COMPONENTS
            ],
        }
        _emit(payload)
        return 0

    if not non_cargo.NON_CARGO_COMPONENTS:
        print("no non-cargo components registered")
        return 0

    width = max(len(c.name) for c in non_cargo.NON_CARGO_COMPONENTS)
    print("Non-cargo components (handled OUTSIDE the cargo recipes lane):")
    for c in non_cargo.NON_CARGO_COMPONENTS:
        print(f"  {c.name:<{width}}  [{c.recipe_kind}]")
        print(f"      handled by: {c.handled_by}")
        print(f"      {c.note}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
