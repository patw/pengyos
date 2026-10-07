"""Validate a :class:`~pengyos_layout.model.Plan` against the layout invariants.

The validator runs every check in :data:`~pengyos_layout.invariants.CHECKS` and
either returns the (possibly empty) list of violation messages or raises
:class:`~pengyos_layout.model.ValidationError`. It also exposes each check
individually so a test can mutate a plan to fire exactly one invariant.
"""

from __future__ import annotations

from typing import List

from .invariants import CHECKS
from .model import Plan, ValidationError


class Validator:
    """Runs the layout invariants against a :class:`Plan`."""

    def __init__(self, checks: List[tuple[str, object]] | None = None) -> None:
        # ``object`` accepts the Callable[[Plan], str | None] tuples without
        # importing typing at call sites.
        self.checks = checks if checks is not None else list(CHECKS)

    def violations(self, plan: Plan) -> List[str]:
        """Return the list of invariant violations (empty when valid)."""
        messages: List[str] = []
        for name, check in self.checks:
            try:
                msg = check(plan)
            except Exception as exc:  # a crashing check is itself a violation
                messages.append(f"{name}: check raised {exc!r}")
            if msg:
                messages.append(msg)
        return messages

    def is_valid(self, plan: Plan) -> bool:
        return not self.violations(plan)

    def validate(self, plan: Plan) -> None:
        """Raise :class:`ValidationError` listing every violation, else return."""
        messages = self.violations(plan)
        if messages:
            raise ValidationError(messages)

    def check_one(self, name: str, plan: Plan) -> str | None:
        """Run a single named check (used by mutation tests)."""
        for check_name, check in self.checks:
            if check_name == name:
                return check(plan)  # type: ignore[arg-type]
        raise KeyError(f"unknown check: {name}")
