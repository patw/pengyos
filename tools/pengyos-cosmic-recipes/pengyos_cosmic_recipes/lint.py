"""``lint`` — the dependency-closure linter for the recipes lane.

The closure linter closes the loop the manifest opens: the manifest declares
components and their inter-component ``deps``; this linter asserts that every
component — and every component its deps point at — actually has a Buildroot
recipe on disk, and that each such recipe is lint-clean.

### What it checks

Given the manifest and a Buildroot ``package/`` directory, it verifies, per
component:

1. **has a recipe** — ``<pkg>/Config.in``, ``<pkg>.mk`` and ``<pkg>.hash`` all
   exist under the recipe root. This is the "has a recipe" half of the closure.
2. **recipe is lint-clean** — the engine's :func:`pengyos_cosmic_pkg.lint.lint_dir`
   finds no problem (hash present, version pinned, license declared, no floating
   git ref). This is the "lint-clean" half.
3. **deps are closed** — every component in ``deps`` also has a recipe. Since
   the recipes-lane manifest declares deps by *name only* (every dep is another
   component in the manifest), a dep is closed exactly when its component has a
   recipe. This is the "every non-Buildroot dependency has a recipe" half: the
   only deps tracked here are inter-component ones (Buildroot system libraries
   are Buildroot symbols, out of scope for this recipes lane).

A report with an empty ``violations`` tuple is closed; the CLI exits 0. Any
violation is a *reported* condition (exit 1), never a crash.

"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from pengyos_cosmic_pkg.lint import LintReport, lint_dir

from .manifest import CosmicManifest

#: The recipe file the engine writes for ``<pkg>`` that has a bare name
#: (everything else is named ``<pkg>.<suffix>``).
_CONFIG_IN = "Config.in"
#: The suffix of the Buildroot cargo package recipe (``<pkg>.mk``).
_MK_SUFFIX = ".mk"
#: The suffix of the Buildroot hash file (``<pkg>.hash``).
_HASH_SUFFIX = ".hash"


@dataclass(frozen=True)
class ClosureViolation:
    """One closure problem: the rule broken and what is wrong.

    ``rule`` is a short, stable identifier — ``missing-recipe``,
    ``unclosed-dep`` or one of the engine's lint rules (``hash`` / ``version`` /
    ``license`` / ``floating-ref``) — so a consumer can classify failures.
    """

    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.message}"


@dataclass(frozen=True)
class ClosureReport:
    """The outcome of :func:`closure_lint`: the manifest linted and its violations.

    ``components`` is every component checked (file order). ``violations`` is the
    ordered list of problems; an empty tuple means the closure is complete.
    ``is_closed`` (and thus ``bool(report)``) is the single gate a caller
    dispatches on.
    """

    manifest_path: Path
    recipe_root: Path
    components: tuple[str, ...]
    violations: tuple[ClosureViolation, ...] = field(default_factory=tuple)

    @property
    def is_closed(self) -> bool:
        """``True`` when every component has a lint-clean recipe and every dep is closed."""
        return not self.violations

    def __bool__(self) -> bool:
        return self.is_closed

    def __str__(self) -> str:
        if self.is_closed:
            return (
                f"{self.manifest_path}: closure complete "
                f"({len(self.components)} component(s) have lint-clean recipes)"
            )
        lines = [
            f"{self.manifest_path}: {len(self.violations)} closure violation(s)"
        ]
        for violation in self.violations:
            lines.append(f"  - {violation}")
        return "\n".join(lines)


def _has_recipe(pkg_dir: Path, pkg: str) -> bool:
    """``True`` when ``pkg_dir`` carries ``Config.in``, ``<pkg>.mk`` and ``<pkg>.hash``."""
    return (
        (pkg_dir / _CONFIG_IN).is_file()
        and (pkg_dir / f"{pkg}{_MK_SUFFIX}").is_file()
        and (pkg_dir / f"{pkg}{_HASH_SUFFIX}").is_file()
    )


def closure_lint(
    manifest: CosmicManifest,
    recipe_root: Path | str,
) -> ClosureReport:
    """Lint the recipe closure for ``manifest`` against ``recipe_root``.

    * ``manifest`` — the loaded recipes-lane manifest (see
      :mod:`pengyos_cosmic_recipes.manifest`).
    * ``recipe_root`` — the Buildroot ``package/`` directory; each component's
      recipe is expected at ``recipe_root/<pkg>/``.

    Checks, in order, per component (file order):

    1. **has a recipe** — ``Config.in`` / ``<pkg>.mk`` / ``<pkg>.hash`` all exist.
    2. **deps are closed** — every declared dep's component also has a recipe.
    3. **recipe is lint-clean** — the engine's :func:`lint_dir` finds no problem
       (only when the recipe is present; a missing recipe is already reported
       above).

    Returns a :class:`ClosureReport`.
    """
    recipe_root = Path(recipe_root)
    entries = manifest.components

    violations: list[ClosureViolation] = []

    # 1. has a recipe — every component must ship Config.in, <pkg>.mk and
    #    <pkg>.hash under the recipe root.
    present: dict[str, Path] = {}
    for entry in entries:
        pkg_dir = recipe_root / entry.name
        if _has_recipe(pkg_dir, entry.name):
            present[entry.name] = pkg_dir
        else:
            expected = ["Config.in", f"{entry.name}.mk", f"{entry.name}.hash"]
            missing = [n for n in expected if not (pkg_dir / n).is_file()]
            violations.append(
                ClosureViolation(
                    "missing-recipe",
                    f"{entry.name}: missing recipe file(s) "
                    f"{', '.join(missing)} under {recipe_root}",
                )
            )

    # 2. deps are closed — every declared dep's component has a recipe.
    for entry in entries:
        for dep in entry.deps:
            if dep not in present:
                violations.append(
                    ClosureViolation(
                        "unclosed-dep",
                        f"{entry.name}: depends on '{dep}', which has no recipe "
                        f"under {recipe_root}",
                    )
                )

    # 3. recipe is lint-clean — the engine checks hash/version/license/floats.
    for entry in entries:
        pkg_dir = present.get(entry.name)
        if pkg_dir is None:
            continue  # already reported as missing-recipe above
        report: LintReport = lint_dir(pkg_dir)
        for violation in report.violations:
            violations.append(
                ClosureViolation(
                    violation.rule,
                    f"{entry.name}: {violation}",
                )
            )

    return ClosureReport(
        manifest_path=manifest.path,
        recipe_root=recipe_root,
        components=tuple(entry.name for entry in entries),
        violations=tuple(violations),
    )


def _emit(args, report: ClosureReport) -> int:
    """Print the closure result (human or JSON) and return the exit code."""
    if getattr(args, "json", False):
        import json

        payload = {
            "command": args.command,
            "ok": report.is_closed,
            "manifest": str(report.manifest_path),
            "recipe_root": str(report.recipe_root),
            "components": list(report.components),
            "violations": [
                {"rule": v.rule, "message": str(v)} for v in report.violations
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif report.is_closed:
        print(str(report))
    else:
        print(str(report), file=sys.stderr)
    return 0 if report.is_closed else 1


def main(args) -> int:
    """CLI entry point for ``lint``."""
    from . import load_manifest

    manifest = load_manifest(args.manifest)
    report = closure_lint(manifest, args.dir)
    return _emit(args, report)
