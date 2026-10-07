"""``pengyos-cosmic-native-deps`` command-line interface.

Four subcommands (see ``spec.md``):

``list``
    Enumerate the native dependencies COSMIC may need, each tagged as shipped
    (2026.08 already provides it — no recipe) or recipe-needed (2026.08 does
    not — a recipe is required), with each one's kconfig symbol(s). Pure — no
    side effects.

``classify [--json]``
    Classify every candidate against the pinned Buildroot 2026.08 symbol list
    and print the per-candidate status (shipped / recipe-needed) with the
    reason. The machine view is the committed ``data/classification.json``.

``recipes [--json]``
    Show the authored recipe catalogue (``data/recipes.json``): for every
    dependency that needs a recipe, its pinned version, upstream URL, build
    system, license, source filename, and sha256. The machine view is the
    committed ``data/recipes.json``.

``emit --out DIR``
    Write the BR2_EXTERNAL recipes (``package/<dep>/{Config.in,<dep>.mk,
    <dep>.hash}``) and the ``fragments/cosmic-native.fragment`` into ``DIR``.
    Mirrors the existing BR2_EXTERNAL recipe style.

``lint --dir DIR``
    Lint the recipes under ``DIR``: every recipe must have a hash, a pinned
    version, and a declared license, and its build-system infra must be correct.

``verify [--names NAME ...] [--json OUT]``
    Re-download each buildable recipe's upstream tarball (network) and re-check
    its pinned sha256. Exits non-zero if any recipe is unverified. This is the
    "source + hash" rung of the verification ladder; it never compiles.

Exit codes: ``0`` clean, ``1`` an operation ran but reported a failure (clean,
no traceback), ``2`` bad arguments.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from . import __version__, catalogue
from .classify import ClassificationReport, classify_from_path
from .emit import emit
from .lint import LintReport, lint_dir
from .recipes import (
    RecipeCatalogue,
    default_recipe_catalogue,
    default_recipe_catalogue_path,
)
from .verify import DownloadStatus, verify_catalogue


# --- Exit codes -------------------------------------------------------------
EXIT_OK: int = 0  # success
EXIT_FAIL: int = 1  # an operation ran but reported a failure (clean, no traceback)
EXIT_USAGE: int = 2  # bad/missing arguments


class CliError(Exception):
    """A user-facing CLI failure.

    Raised for every clean, non-crash failure. The :func:`main` entry point maps
    it to a one-line ``pengyos-cosmic-native-deps: error: ...`` message on
    stderr and exit 1 — never a raw traceback.
    """


# --- Argument surface -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser.

    Pure: building the parser has no side effects, which makes it directly
    unit-testable (parsing, defaults, help text).
    """
    parser = argparse.ArgumentParser(
        prog="pengyos-cosmic-native-deps",
        description=(
            "Emit and lint Buildroot BR2_EXTERNAL recipes for the native "
            "(non-Rust) dependencies COSMIC needs that 2026.08 does not ship."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list",
        help="List the COSMIC native deps and their shipped/missing status.",
        description=(
            "List the native dependencies COSMIC may need, each tagged as "
            "shipped (2026.08 already provides it — no recipe) or missing "
            "(2026.08 does not — a recipe is required)."
        ),
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the candidate list as JSON instead of a table.",
    )
    list_parser.set_defaults(func=_cmd_list)

    classify_parser = subparsers.add_parser(
        "classify",
        help="Classify each candidate as shipped or recipe-needed.",
        description=(
            "Classify every native dependency COSMIC may need against the "
            "pinned Buildroot 2026.08 symbol list, tagging each as shipped "
            "(2026.08 already provides it — no recipe) or recipe-needed "
            "(2026.08 does not — a recipe is required), with the reason. The "
            "machine view is the committed data/classification.json."
        ),
    )
    classify_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the classification report as JSON instead of a table.",
    )
    classify_parser.add_argument(
        "--symbols",
        metavar="PATH",
        default=None,
        help=(
            "Override the symbol list to classify against (default: the "
            "committed data/buildroot-2026.08-symbols.txt)."
        ),
    )
    classify_parser.set_defaults(func=_cmd_classify)

    recipes_parser = subparsers.add_parser(
        "recipes",
        help="Show the authored recipe catalogue (version, URL, license, hash).",
        description=(
            "Show the authored BR2_EXTERNAL recipe catalogue "
            "(data/recipes.json): for every dependency that needs a recipe, "
            "its pinned version, upstream URL, build system, license, source "
            "filename, and sha256. The machine view is the committed "
            "data/recipes.json."
        ),
    )
    recipes_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the recipe catalogue as JSON instead of a table.",
    )
    recipes_parser.set_defaults(func=_cmd_recipes)

    verify_parser = subparsers.add_parser(
        "verify",
        help="Re-check each buildable recipe's pinned sha256 vs its tarball.",
        description=(
            "Re-download each buildable recipe's upstream tarball from its "
            "download_url and re-check the pinned sha256. Exits non-zero if any "
            "recipe is unverified (hash mismatch, or the endpoint answered with "
            "a non-gzip challenge page). This is the 'source + hash' rung of the "
            "verification ladder: it never compiles. Needs network."
        ),
    )
    verify_parser.add_argument(
        "--names",
        nargs="+",
        metavar="NAME",
        default=None,
        help=(
            "Verify only these recipe names (default: every buildable recipe)."
        ),
    )
    verify_parser.add_argument(
        "--json",
        metavar="OUT",
        help="Write a JSON verify report to OUT instead of the text report.",
    )
    verify_parser.set_defaults(func=_cmd_verify)

    emit_parser = subparsers.add_parser(
        "emit",
        help="Write the BR2_EXTERNAL recipes and the enabling fragment.",
        description=(
            "Write the BR2_EXTERNAL recipes for every missing native dep "
            "(package/<dep>/{Config.in,<dep>.mk,<dep>.hash}) plus "
            "fragments/cosmic-native.fragment into --out DIR."
        ),
    )
    emit_parser.add_argument(
        "--out",
        required=True,
        metavar="DIR",
        help="The BR2_EXTERNAL tree root to write recipes into.",
    )
    emit_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the emit report as JSON instead of a one-line summary.",
    )
    emit_parser.set_defaults(func=_cmd_emit)

    lint_parser = subparsers.add_parser(
        "lint",
        help="Lint the recipes under a directory.",
        description=(
            "Lint the BR2_EXTERNAL recipes under --dir DIR: every recipe must "
            "have a hash, a pinned version, and a declared license, and its "
            "build-system infra must be correct. Exits non-zero on any "
            "violation so it can gate CI."
        ),
    )
    lint_parser.add_argument(
        "--dir",
        required=True,
        metavar="DIR",
        help="The directory to scan for recipes.",
    )
    lint_parser.add_argument(
        "--json",
        metavar="OUT",
        help="Write a JSON lint report to OUT instead of the text report.",
    )
    lint_parser.set_defaults(func=_cmd_lint)

    return parser


# --- Command handlers -------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    """Print the candidate dependency list, each tagged shipped / recipe-needed.

    This is real: the candidate set lives in :mod:`.catalogue` and the
    shipped/missing classification against the vendored 2026.08 symbol list lives
    in :mod:`.classify`. The ``--json`` flag emits the machine view.
    """
    import json

    report = classify_from_path()
    payload = [
        {
            "name": entry.name,
            "status": entry.status.value,
            "br_symbols": list(entry.br_symbols),
            "description": _description_for(entry.name),
        }
        for entry in report.entries
    ]
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_list_table(payload)
    return EXIT_OK


def _description_for(name: str) -> str:
    """The human description for *name* (falls back to a blank line)."""
    for dep in catalogue.candidates():
        if dep.name == name:
            return dep.description
    return ""


def _print_list_table(payload: list[dict[str, str]]) -> None:
    """Render the ``list`` payload as a stable, aligned text table.

    Columns: name, status, then the kconfig symbol(s). The description follows on
    the next line, indented under the name.
    """
    name_width = max((len(dep["name"]) for dep in payload), default=0)
    status_width = max((len(dep["status"]) for dep in payload), default=0)
    for dep in payload:
        symbols = ", ".join(dep["br_symbols"])
        print(f"{dep['name']:<{name_width}}  {dep['status']:<{status_width}}  {symbols}")
        print(f"{'':<{name_width}}  {'':<{status_width}}  {dep['description']}")


def _cmd_classify(args: argparse.Namespace) -> int:
    """Print the full classification report (shipped / recipe-needed per dep).

    Real: the report is computed against the vendored 2026.08 symbol list via
    :func:`.classify.classify_from_path`. The ``--json`` flag emits the machine
    view (identical in shape to the committed ``data/classification.json``).
    """
    import json

    report = classify_from_path(getattr(args, "symbols", None))
    if getattr(args, "json", False):
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        _print_classify_table(report)
    return EXIT_OK


def _print_classify_table(report: ClassificationReport) -> None:
    """Render the classification report as a stable, aligned text table."""
    name_width = max(len(entry.name) for entry in report.entries)
    status_width = max(len(entry.status.value) for entry in report.entries)
    print(
        f"Buildroot {report.tag} symbol list — {report.source}"
    )
    print()
    for entry in report.entries:
        print(
            f"{entry.name:<{name_width}}  {entry.status.value:<{status_width}}  {entry.reason}"
        )


def _cmd_recipes(args: argparse.Namespace) -> int:
    """Print the authored recipe catalogue (version, URL, license, hash per dep).

    Real: the catalogue is the committed ``data/recipes.json`` loaded via
    :func:`.recipes.default_recipe_catalogue`. The ``--json`` flag emits the
    machine view (identical in shape to the committed data file).
    """
    import json

    catalogue_recipes = default_recipe_catalogue()
    payload = [recipe.as_dict() for recipe in catalogue_recipes]
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_recipes_table(payload)
    return EXIT_OK


def _print_recipes_table(payload: list[dict[str, str]]) -> None:
    """Render the ``recipes`` payload as a stable, aligned text table.

    Columns: name, version, build system, license, then the source filename.
    Data-only (font) recipes show ``-`` for version/source and their sha256 is
    empty, so those cells read ``-`` too.
    """
    names = [dep["name"] for dep in payload]
    name_width = max((len(n) for n in names), default=0)
    versions = [dep.get("version") or "-" for dep in payload]
    buildsystems = [dep["build_system"] for dep in payload]
    licenses = [dep["license"] for dep in payload]
    sources = [dep.get("source") or "-" for dep in payload]
    widths = [
        max(len(x) for x in names),
        max(len(x) for x in versions),
        max(len(x) for x in buildsystems),
        max(len(x) for x in licenses),
        max(len(x) for x in sources),
    ]
    for name, version, build_system, license_, source in zip(
        names, versions, buildsystems, licenses, sources
    ):
        print(
            f"{name:<{widths[0]}}  {version:<{widths[1]}}  "
            f"{build_system:<{widths[2]}}  {license_:<{widths[3]}}  {source:<{widths[4]}}"
        )


def _cmd_verify(args: argparse.Namespace) -> int:
    """Re-check each buildable recipe's pinned sha256 against its tarball.

    Real: this downloads each recipe's upstream tarball (network) and re-checks
    the digest recorded in :mod:`.recipes`. Data-only recipes are reported as
    verified with no download. Exits non-zero if any recipe is unverified
    (hash mismatch, or the endpoint answered with a non-gzip challenge page) so
    it can gate CI.
    """
    import json

    recipes = default_recipe_catalogue()
    wanted = getattr(args, "names", None)
    if wanted:
        known = {recipe.name for recipe in recipes}
        unknown = [name for name in wanted if name not in known]
        if unknown:
            raise CliError(
                "unknown recipe name(s): " + ", ".join(unknown)
                + f" (known: {', '.join(sorted(known))})"
            )
        selected = [recipe for recipe in recipes if recipe.name in set(wanted)]
    else:
        selected = list(recipes)

    results = verify_catalogue(RecipeCatalogue(tuple(selected)))
    payload = [
        {
            "name": result.recipe.name,
            "status": result.status.value,
            "expected_hash": result.expected_hash,
            "actual_hash": result.actual_hash,
            "detail": result.detail,
        }
        for result in results
    ]

    out_path = getattr(args, "json", None)
    if out_path:
        report = {
            "catalogue": str(default_recipe_catalogue_path()),
            "results": payload,
            "summary": _summary_counts(results),
        }
        resolved = Path(out_path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote verify report to {resolved}")
    else:
        _print_verify_table(payload)

    unverified = [result for result in results if result.status is not DownloadStatus.VERIFIED]
    return EXIT_OK if not unverified else EXIT_FAIL


def _summary_counts(results) -> dict[str, int]:
    """Count verify results by status value (ordered, JSON-friendly)."""
    counts: dict[str, int] = {status.value: 0 for status in DownloadStatus}
    for result in results:
        counts[result.status.value] += 1
    return counts


def _print_verify_table(payload: list[dict[str, str]]) -> None:
    """Render the ``verify`` payload as a stable, aligned text table."""
    name_width = max((len(dep["name"]) for dep in payload), default=0)
    status_width = max((len(dep["status"]) for dep in payload), default=0)
    for dep in payload:
        print(
            f"{dep['name']:<{name_width}}  {dep['status']:<{status_width}}  "
            f"{dep['detail']}"
        )


def _cmd_emit(args: argparse.Namespace) -> int:
    """Write the recipes and fragment into ``--out DIR``.

    Real: the recipes are rendered from the committed catalogue via
    :func:`.emit.emit` and written into ``--out`` as a BR2_EXTERNAL tree
    (``package/<dep>/{Config.in,<dep>.mk,<dep>.hash}`` plus
    ``fragments/cosmic-native.fragment``). The rendering is deterministic, so a
    re-run overwrites the files with identical bytes. The ``--json`` flag emits
    the deterministic emit report.
    """
    import json

    catalogue = default_recipe_catalogue()
    report = emit(catalogue, args.out)
    if getattr(args, "json", False):
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"wrote {report.count} file(s) to {report.out_dir}")
    return EXIT_OK


def _cmd_lint(args: argparse.Namespace) -> int:
    """Lint the recipes under ``--dir DIR``.

    Real: scans ``--dir`` for ``package/<dep>/`` recipe directories and runs the
    linter (:func:`.lint.lint_dir`) — every buildable recipe must carry a hash,
    pin a version, declare a license, and select a correct build-system infra.
    Exits non-zero on any violation so it can gate CI. The ``--json`` flag emits
    the deterministic lint report.
    """
    import json

    report = lint_dir(args.dir)
    if getattr(args, "json", None):
        resolved = Path(args.json)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(
            json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote lint report to {resolved}")
    else:
        _print_lint_table(report)

    return EXIT_OK if report.ok else EXIT_FAIL


def _print_lint_table(report: LintReport) -> None:
    """Render the ``lint`` report as a stable, aligned text table.

    Columns: name, kind, status, then the first issue (or ``-`` when clean).
    """
    name_width = max((len(r.name) for r in report.results), default=0)
    status_width = max((len(r.status.value) for r in report.results), default=0)
    for result in report.results:
        detail = result.issues[0].message if result.issues else "-"
        kind_width = max((len(result.kind.value) for result in report.results), default=len("buildable"))
        print(
            f"{result.name:<{name_width}}  "
            f"{result.kind.value:<{kind_width}}  "
            f"{result.status.value:<{status_width}}  {detail}"
        )
    summary = (
        f"{len(report.results)} recipe(s), "
        f"{len(report.violations)} violation(s)"
    )
    print()
    print(summary)


# --- Entry point ------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code (0 = success)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CliError as exc:
        # The single, user-facing failure type: a clean one-line message and a
        # non-zero exit, never a traceback.
        print(f"pengyos-cosmic-native-deps: error: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
