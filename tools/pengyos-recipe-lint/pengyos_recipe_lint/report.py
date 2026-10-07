"""Report layer: turn ``RuleResult`` rows into ``file:line`` violations.

Layering: ``recipes`` -> ``recipe_parser`` -> ``rules`` ->
``report`` -> ``cli``.

The rule layer (``pengyos_recipe_lint.rules``) answers one question per rule
and returns a structured :class:`~pengyos_recipe_lint.rules.RuleResult`
(``rule``, ``ok``, ``message``, ``line_no``, fix ``hint``).  This layer owns
*output*: it attaches the recipe's path to build a ``file:line`` location,
orders the violations deterministically, renders them as text or JSON, and
derives the process exit code (``0`` when clean, ``1`` when any violation is
present).

It is deliberately free of argparse / stdout concerns so it can be unit-tested
in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .recipe_parser import RecipeSource
from .recipes import Recipe
from .rules import check_recipe


@dataclass(frozen=True)
class Violation:
    """A single lint failure, tied to a recipe and carrying its fix hint."""

    recipe: str
    """Recipe name (the ``.mk`` stem)."""

    rule: str
    """The failed rule's id (``RuleId`` value, e.g. ``"license_declared"``)."""

    file: str
    """Path to the offending recipe."""

    line: int | None
    """1-based source line of the offending assignment, or ``None`` when the
    rule could not point at one (e.g. a missing variable)."""

    message: str
    """Human-readable description of the failure."""

    hint: str | None
    """Concrete next step, or ``None`` when the rule offers none."""

    @property
    def location(self) -> str:
        """The ``file:line`` rendering (``file`` alone when there is no line)."""
        if self.line is None:
            return self.file
        return f"{self.file}:{self.line}"

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable view used by the CLI's ``--json`` output."""
        return {
            "recipe": self.recipe,
            "rule": self.rule,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass
class Report:
    """The full result of linting a set of recipes."""

    recipes: int
    """Number of recipes that were linted."""

    violations: list[Violation] = field(default_factory=list)
    """Every failing rule, in stable order (recipe path, then rule order)."""

    @property
    def violation_count(self) -> int:
        """How many rule failures were found."""
        return len(self.violations)

    @property
    def exit_code(self) -> int:
        """``0`` when clean, ``1`` when any violation is present."""
        return 1 if self.violations else 0

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable view of the violations (rule ids as their values)."""
        return {
            "violation_count": self.violation_count,
            "violations": [v.to_dict() for v in self.violations],
        }

    def render_text(self) -> str:
        """Human-readable, stable rendering of the report.

        Violations are grouped by recipe file (in path order); within a recipe
        they follow :data:`pengyos_recipe_lint.rules.RULE_ORDER`.  Each violation
        prints its ``file:line`` location, the rule id, the message, and its fix
        hint.
        """
        if self.violation_count == 0:
            return f"OK: {self.recipes} recipe(s), 0 violations\n"

        grouped: dict[str, list[Violation]] = {}
        for violation in self.violations:
            grouped.setdefault(violation.file, []).append(violation)

        lines = [
            f"{self.violation_count} violation(s) in {self.recipes} recipe(s)",
            "",
        ]
        for recipe_file in sorted(grouped):
            lines.append(recipe_file)
            for violation in grouped[recipe_file]:
                lines.append(
                    f"  {violation.location}  {violation.rule}  {violation.message}"
                )
                if violation.hint:
                    lines.append(f"      hint: {violation.hint}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def lint_recipes(recipes: Sequence[Recipe]) -> Report:
    """Run every implemented rule against every recipe and collect violations.

    Each recipe is parsed once and checked via
    :func:`pengyos_recipe_lint.rules.check_recipe`.  Violations are ordered by
    recipe path (ascending) and then by the rule order in
    :data:`pengyos_recipe_lint.rules.RULE_ORDER`, so the output is stable
    regardless of filesystem walk order.
    """
    violations: list[Violation] = []
    for recipe in sorted(recipes, key=lambda r: r.path):
        source = RecipeSource.from_file(recipe.path)
        for result in check_recipe(recipe, source):
            if not result.ok:
                violations.append(
                    Violation(
                        recipe=recipe.name,
                        rule=result.rule.value,
                        file=str(recipe.path),
                        line=result.line_no,
                        message=result.message,
                        hint=result.hint,
                    )
                )
    return Report(recipes=len(recipes), violations=violations)
