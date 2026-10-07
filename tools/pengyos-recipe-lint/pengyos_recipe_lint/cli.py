"""``pengyos-recipe-lint`` command-line interface.

Subcommands (see ``spec.md``):

* ``check <dir> [--json OUT]`` — discover every recipe under *dir* and report.
* ``fix-hints <recipe.mk>`` — print fix hints for a single recipe.

Exit codes: ``0`` clean (no violations / hints printed), ``1`` violations found
(by ``check``) or a runtime error, ``2`` bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .recipe_parser import RecipeSource
from .recipes import discover_recipe, discover_recipes
from .report import lint_recipes
from .rules import check_recipe


def _cmd_check(args: argparse.Namespace) -> int:
    """Discover recipes under a directory, lint them, and report violations.

    Prints a stable ``file:line`` report (with a fix hint per violation) to
    stdout, or a JSON report to ``--json OUT``.  Returns a non-zero exit code
    when any violation is found so the command can gate CI.
    """
    try:
        recipes = discover_recipes(args.dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"pengyos-recipe-lint: {exc}", file=sys.stderr)
        return 1

    report = lint_recipes(recipes)

    if args.json:
        payload = {
            "version": __version__,
            "count": len(recipes),
            "recipes": [r.to_dict() for r in recipes],
            **report.to_dict(),
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        print(f"wrote {payload['count']} recipe(s) to {args.json}")
    else:
        print(report.render_text(), end="")

    return report.exit_code


def _cmd_fix_hints(args: argparse.Namespace) -> int:
    """Print fix hints for a single recipe.

    Runs every rule against the recipe and prints each violation with its
    ``file:line`` location and concrete fix hint.  Returns ``1`` when the
    recipe has violations (matching the linter's exit-code contract) and ``0``
    when it is clean.
    """
    try:
        recipe = discover_recipe(args.recipe)
    except (FileNotFoundError, ValueError) as exc:
        print(f"pengyos-recipe-lint: {exc}", file=sys.stderr)
        return 1

    source = RecipeSource.from_file(recipe.path)
    results = check_recipe(recipe, source)
    violations = [result for result in results if not result.ok]

    print(f"recipe: {recipe.name}")
    print(f"path:   {recipe.path}")
    if recipe.hash_path is not None:
        print(f"hash:   {recipe.hash_path} (present)")
    else:
        print(f"hash:   (missing sibling {recipe.path.with_suffix('.hash')})")

    if not violations:
        print("status: OK — no violations")
        return 0

    print(f"status: {len(violations)} violation(s)")
    for result in violations:
        location = (
            str(recipe.path)
            if result.line_no is None
            else f"{recipe.path}:{result.line_no}"
        )
        print(f"  {location}  {result.rule.value}  {result.message}")
        if result.hint:
            print(f"      hint: {result.hint}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pengyos-recipe-lint",
        description="Lint PengyOS Buildroot package recipes.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="lint every recipe under a directory")
    check.add_argument("dir", help="directory to scan for .mk recipes")
    check.add_argument("--json", metavar="OUT", help="write a JSON report to OUT")
    check.set_defaults(func=_cmd_check)

    hints = subparsers.add_parser("fix-hints", help="print fix hints for a single recipe")
    hints.add_argument("recipe", help="path to a .mk recipe")
    hints.set_defaults(func=_cmd_fix_hints)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
