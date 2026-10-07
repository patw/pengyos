"""Prometheus-style alert-rule evaluator (US-SRV-3).

Evaluates the small, documented rule subset (see
:mod:`pengyos_monitoring.rules`) against a set of metric
:class:`pengyos_monitoring.series.Series`. No Prometheus or exporter is
required — the evaluator runs over committed fixture series.

Evaluation model
----------------
* ``ratio("a", "b") OP number`` — find the series named ``a`` and ``b`` by
  label-set, divide their aligned values, and compare.
* ``metric{labels} OP number`` — find the series matching ``metric`` and the
  optional label matchers, take its value, and compare.
* **Sustained-for**: the rule fires only if the comparison holds across a
  contiguous run of samples ending at the latest sample and spanning at least
  ``for`` seconds (``last_ts - run_start_ts >= for``). A single sample spans
  zero seconds, so with any strictly-positive ``for`` it never fires.

The result is an :class:`Alert` when the rule fires, otherwise ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from pengyos_monitoring.rules import Rule
from pengyos_monitoring.series import Series

_CMP_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

# ratio("name{labels}", "name{labels}")
_RATIO_RE = re.compile(
    r'^ratio\(\s*"([A-Za-z_:][A-Za-z0-9_:]*)"((?:\{[^}]*\})?)\s*,\s*'
    r'"([A-Za-z_:][A-Za-z0-9_:]*)"((?:\{[^}]*\})?)\s*\)$'
)

# name{labels}  or  name
_METRIC_RE = re.compile(
    r'^([A-Za-z_:][A-Za-z0-9_:]*)((?:\{[^}]*\})?)$'
)

_LABEL_RE = re.compile(r'''([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"''')


@dataclass(frozen=True)
class _Operand:
    """A parsed operand: a metric name plus optional label matchers."""

    name: str
    labels: dict[str, str]


@dataclass
class Alert:
    """A fired alert, rendered from a rule and the sample that triggered it."""

    rule_name: str
    severity: str
    summary: str
    expr: str
    series_name: str
    value: float
    timestamp: float
    for_seconds: int


def _parse_labels(brace_text: str) -> dict[str, str]:
    if not brace_text:
        return {}
    return {m.group(1): m.group(2) for m in _LABEL_RE.finditer(brace_text)}


def _parse_operand(operand_text: str) -> _Operand:
    m = _METRIC_RE.match(operand_text)
    if m:
        return _Operand(name=m.group(1), labels=_parse_labels(m.group(2) or ""))
    raise ValueError(f"cannot parse operand {operand_text!r}")


def _parse_ratio(operand_text: str) -> tuple[_Operand, _Operand]:
    m = _RATIO_RE.match(operand_text)
    if not m:
        raise ValueError(f"ratio operand {operand_text!r} is not parseable")
    a = _Operand(m.group(1), _parse_labels(m.group(2) or ""))
    b = _Operand(m.group(3), _parse_labels(m.group(4) or ""))
    return a, b


def _find_series(
    series_list: Iterable[Series], operand: _Operand
) -> Series | None:
    """Return the single series matching ``operand.name`` and labels."""
    matches = [s for s in series_list if s.name == operand.name and s.matches(operand.labels)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"multiple series match {operand.name}{operand.labels}: "
            f"{[s.labels for s in matches]}"
        )
    return None


def _aligned_timestamps(*series: Series | None) -> list[float]:
    """Timestamps present in *all* given series, ascending."""
    present = [set(s.samples_timestamps()) for s in series if s is not None]
    if not present:
        return []
    common = present[0]
    for p in present[1:]:
        common &= p
    return sorted(common)


def _sustained_fire(values_by_ts: dict[float, float], rule: Rule) -> bool:
    """Whether the condition holds for a run spanning at least ``for`` seconds.

    Semantics: the run must end at the latest sample (the alert is "current").
    Walk backwards from the latest sample while the comparison holds; the alert
    fires when that backward run spans at least ``rule.for_`` seconds
    (``last_ts - run_start_ts >= for``). A single sample spans zero seconds, so
    with any strictly-positive ``for`` it never fires.
    """
    if not values_by_ts:
        return False
    ordered = sorted(values_by_ts)
    compare = _CMP_OPS[rule.expr.comparison]
    run_end = ordered[-1]
    if not compare(values_by_ts[run_end], rule.expr.rhs):
        return False
    run_start = run_end
    for ts in reversed(ordered[:-1]):
        if compare(values_by_ts[ts], rule.expr.rhs):
            run_start = ts
        else:
            break
    return (run_end - run_start) >= rule.for_.seconds


def evaluate(rule: Rule, series_list: Iterable[Series]) -> Alert | None:
    """Evaluate ``rule`` against ``series_list``.

    Returns a :class:`Alert` if the rule fires (comparison holds sustained for
    ``for`` seconds), otherwise ``None``.
    """
    expr = rule.expr
    if expr.operand_kind == "ratio":
        return _evaluate_ratio(rule, series_list)
    return _evaluate_metric(rule, series_list)


def _evaluate_ratio(rule: Rule, series_list: Iterable[Series]) -> Alert | None:
    a, b = _parse_ratio(rule.expr.operand)
    series_a = _find_series(series_list, a)
    series_b = _find_series(series_list, b)
    if series_a is None or series_b is None:
        return None

    values_by_ts: dict[float, float] = {}
    for ts in _aligned_timestamps(series_a, series_b):
        va = series_a.value_at(ts)
        vb = series_b.value_at(ts)
        if vb == 0:
            # Division by zero: treat as not-evaluable (does not fire).
            continue
        values_by_ts[ts] = float(Decimal(str(va)) / Decimal(str(vb)))

    if not _sustained_fire(values_by_ts, rule):
        return None
    ts = max(values_by_ts)
    return Alert(
        rule_name=rule.name,
        severity=rule.severity,
        summary=rule.summary,
        expr=rule.expr.raw,
        series_name=f"{rule.name}[ratio={values_by_ts[ts]:.4g}]",
        value=values_by_ts[ts],
        timestamp=ts,
        for_seconds=rule.for_.seconds,
    )


def _evaluate_metric(rule: Rule, series_list: Iterable[Series]) -> Alert | None:
    operand = _parse_operand(rule.expr.operand)
    series = _find_series(series_list, operand)
    if series is None:
        return None
    values_by_ts = {s.timestamp: s.value for s in series.samples}
    if not _sustained_fire(values_by_ts, rule):
        return None
    ts = max(values_by_ts)
    return Alert(
        rule_name=rule.name,
        severity=rule.severity,
        summary=rule.summary,
        expr=rule.expr.raw,
        series_name=series.name,
        value=values_by_ts[ts],
        timestamp=ts,
        for_seconds=rule.for_.seconds,
    )
