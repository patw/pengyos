"""Alert rules as data for PengyOS server-monitoring (US-SRV-3).

This module defines the *rule schema* and the small, documented evaluator
subset. It loads ``*.toml`` rule files, validates their schema, and validates
that each ``expr`` conforms to the supported grammar. The actual evaluation of
an expression over a metric *series* lives in :mod:`pengyos_monitoring.evaluator`.

Rule schema (TOML)
------------------
Each rule is a ``[[rule]]`` table with the fields Prometheus-style alerting
uses, narrowed to what this evaluator understands:

.. code-block:: toml

    [[rule]]
    name        = "disk-low-free-ratio"   # required, unique, [A-Za-z_][A-Za-z0-9_-]*
    expr        = "ratio(a, b) < 0.10"    # required, grammar-bounded
    for         = "5m"                    # required, duration (see Duration)
    severity    = "critical"              # required, one of SEVERITIES
    summary     = "Disk free ratio below 10%."   # required, non-empty

Evaluator subset (grammar)
--------------------------
``expr`` is intentionally small and fully documented here so the evaluator can
parse it deterministically without a general expression engine:

.. code-block:: text

    expr    := operand cmp_op rhs
    operand := ratio
              | metric
    ratio   := "ratio(" seriesref "," seriesref ")"
    metric  := identifier ("{" labelkey "=" labelvalue ("," ...)* "}")?
    cmp_op  := "<" | "<=" | ">" | ">=" | "==" | "!="
    rhs     := NUMBER

* ``ratio(a, b)`` — combine two series by matching label-set and divide the
  latest value of ``a`` by the latest value of ``b``.
* ``metric{labels}`` — a single series, optionally filtered by ``{key="value",
  ...}`` label matchers; the latest value is compared to ``rhs``.
* ``rhs`` is a float.

The rule *fires* when the comparison is true **and** has held for the entire
``for`` window (sustained-for semantics), see the evaluator.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

#: Allowed severity levels, ordered least-to-most severe.
SEVERITIES: tuple[str, ...] = ("info", "warning", "critical")

#: Duration units and their multiplier in seconds.
_DURATION_UNITS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

# A duration like "5m", "30s", "1h30m", "2d".
_DURATION_RE = re.compile(r"^((?:\d+[smhd])+)$")

# The whole expr: operand, cmp operator, numeric right-hand side.
_EXPR_RE = re.compile(
    r"^\s*"
    r"(?P<operand>ratio\([^)]*\)|[A-Za-z_:][A-Za-z0-9_:]*(?:\{[^}]*\})?)"
    r"\s*(?P<cmp><=|>=|==|!=|<|>)\s*"
    r"(?P<rhs>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    r"\s*$"
)

# A ratio operand: ratio("name{...}", "name{...}") with quoted series refs.
_RATIO_RE = re.compile(
    r"^ratio\(\s*\"[A-Za-z_:][A-Za-z0-9_:]*(?:\{[^}]*\})?\"\s*,\s*"
    r"\"[A-Za-z_:][A-Za-z0-9_:]*(?:\{[^}]*\})?\"\s*\)$"
)

# A metric operand: identifier with optional {key="value", ...} label set.
_METRIC_RE = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*(?:\{[^}]*\})?$")

# Label set inside braces: key="value", key="value", ...
_LABELS_RE = re.compile(
    r"""^(\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*"[^"]*"(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*"[^"]*"\s*)*)$"""
)


class RuleValidationError(ValueError):
    """Raised when a rule (schema or grammar) is invalid."""


@dataclass(frozen=True)
class Duration:
    """A parsed Prometheus-style duration in whole seconds."""

    seconds: int

    def __str__(self) -> str:
        return f"{self.seconds}s"

    @classmethod
    def parse(cls, text: str) -> "Duration":
        """Parse a duration like ``"5m"`` or ``"1h30m"`` into seconds.

        Raises :class:`RuleValidationError` on malformed input.
        """
        if not isinstance(text, str) or not _DURATION_RE.match(text):
            raise RuleValidationError(
                f"invalid duration {text!r}: expected a sum of "
                f"<number>[smhd] units (e.g. '5m', '1h30m')"
            )
        total = 0
        number = ""
        for ch in text:
            if ch.isdigit():
                number += ch
            else:  # a unit character
                if not number:
                    raise RuleValidationError(
                        f"invalid duration {text!r}: missing number before "
                        f"unit {ch!r}"
                    )
                total += int(number) * _DURATION_UNITS[ch]
                number = ""
        if total <= 0:
            raise RuleValidationError(
                f"invalid duration {text!r}: must be greater than zero"
            )
        return cls(total)


@dataclass(frozen=True)
class Expr:
    """A parsed, grammar-valid expression."""

    raw: str
    operand_kind: str  # "ratio" | "metric"
    comparison: str
    rhs: Decimal

    @property
    def operand(self) -> str:
        """The operand text with surrounding whitespace stripped."""
        return self.raw.split(self.comparison, 1)[0].strip()


@dataclass(frozen=True)
class Rule:
    """A single validated alert rule."""

    name: str
    expr: Expr
    for_: Duration
    severity: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        """Serialize back to the on-disk field representation."""
        return {
            "name": self.name,
            "expr": self.expr.raw,
            "for": str(self.for_),
            "severity": self.severity,
            "summary": self.summary,
        }


def _parse_rhs(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:  # pragma: no cover - guarded by regex
        raise RuleValidationError(f"invalid number rhs {raw!r}") from exc


def validate_expr(expr: str) -> Expr:
    """Validate ``expr`` against the documented grammar.

    Raises :class:`RuleValidationError` if the expression does not conform.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise RuleValidationError("expr must be a non-empty string")

    match = _EXPR_RE.match(expr)
    if match is None:
        raise RuleValidationError(
            f"expr {expr!r} does not match the supported grammar; expected "
            "'ratio(a, b) OP number' or 'metric{labels} OP number'"
        )

    operand = match.group("operand")
    if operand.startswith("ratio("):
        if not _RATIO_RE.match(operand):
            raise RuleValidationError(
                f"ratio operand {operand!r} must be "
                'ratio("series", "series") with quoted series refs'
            )
        operand_kind = "ratio"
    else:
        if not _METRIC_RE.match(operand):
            raise RuleValidationError(
                f"metric operand {operand!r} is not a valid series ref"
            )
        brace = operand.find("{")
        if brace != -1:
            labels = operand[brace + 1 : -1]
            if not _LABELS_RE.match(labels):
                raise RuleValidationError(
                    f"metric operand {operand!r}: label set must be "
                    'key="value", key="value", ...'
                )
        operand_kind = "metric"

    return Expr(
        raw=expr.strip(),
        operand_kind=operand_kind,
        comparison=match.group("cmp"),
        rhs=_parse_rhs(match.group("rhs")),
    )


def _require(table: object, field: str, where: str) -> str:
    if not isinstance(table, dict):
        raise RuleValidationError(f"{where}: rule must be a table")
    if field not in table:
        raise RuleValidationError(f"{where}: missing required field {field!r}")
    value = table[field]
    if not isinstance(value, str) or not value.strip():
        raise RuleValidationError(f"{where}: field {field!r} must be a non-empty string")
    return value


def _validate_rule_table(table: dict[str, object], index: int) -> Rule:
    where = f"rule[{index}]"

    name = _require(table, "name", where)
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", name):
        raise RuleValidationError(
            f"{where}: name {name!r} must match [A-Za-z_][A-Za-z0-9_-]*"
        )

    severity = _require(table, "severity", where).lower()
    if severity not in SEVERITIES:
        raise RuleValidationError(
            f"{where}: severity {severity!r} must be one of "
            f"{', '.join(SEVERITIES)}"
        )

    for_raw = _require(table, "for", where)
    for_ = Duration.parse(for_raw)

    expr_raw = _require(table, "expr", where)
    expr = validate_expr(expr_raw)

    summary = _require(table, "summary", where)

    return Rule(name=name, expr=expr, for_=for_, severity=severity, summary=summary)


def load_rules(path: Path | str) -> list[Rule]:
    """Load and validate alert rules from a TOML file.

    Validates the schema, the grammar of every ``expr``, the ``for`` duration,
    severity membership, and uniqueness of rule names.

    Raises :class:`RuleValidationError` on the first problem found.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RuleValidationError(f"invalid TOML in {path}: {exc}") from exc

    if "rule" not in data:
        raise RuleValidationError(f"{path}: top-level 'rule' array is required")
    rules_raw = data["rule"]
    if not isinstance(rules_raw, list) or not rules_raw:
        raise RuleValidationError(f"{path}: 'rule' must be a non-empty array of tables")

    rules: list[Rule] = []
    seen: set[str] = set()
    for i, table in enumerate(rules_raw):
        rule = _validate_rule_table(table, i)
        if rule.name in seen:
            raise RuleValidationError(f"{path}: duplicate rule name {rule.name!r}")
        seen.add(rule.name)
        rules.append(rule)
    return rules


def load_rules_from_text(text: str, source: str = "<rules>") -> list[Rule]:
    """Like :func:`load_rules` but from an in-memory string."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
        fh.write(text)
        tmp = Path(fh.name)
    try:
        return load_rules(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def validate_rules_iterable(rules: Iterable[Rule]) -> None:
    """Re-validate a set of already-constructed rules (uniqueness + grammar)."""
    seen: set[str] = set()
    for rule in rules:
        if rule.name in seen:
            raise RuleValidationError(f"duplicate rule name {rule.name!r}")
        seen.add(rule.name)
        validate_expr(rule.expr.raw)
