"""Tests for alert-rule schema + grammar validation (task: rules as data)."""

from __future__ import annotations

from pathlib import Path

import pytest

from decimal import Decimal

from pengyos_monitoring.rules import (
    Duration,
    RuleValidationError,
    load_rules,
    load_rules_from_text,
    validate_expr,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_FILE = REPO_ROOT / "rules" / "pengyos.toml"


class TestDuration:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30s", 30),
            ("5m", 300),
            ("1h", 3600),
            ("2d", 172800),
            ("1h30m", 5400),
            ("45s", 45),
        ],
    )
    def test_parse_valid(self, text: str, expected: int) -> None:
        assert Duration.parse(text).seconds == expected

    @pytest.mark.parametrize("text", ["", "5x", "m", "5", "0m", "-5m", "mm", "1.5m"])
    def test_parse_invalid(self, text: str) -> None:
        with pytest.raises(RuleValidationError):
            Duration.parse(text)

    def test_str_roundtrip(self) -> None:
        assert str(Duration.parse("5m")) == "300s"


class TestValidateExpr:
    @pytest.mark.parametrize(
        "expr",
        [
            'ratio("a", "b") < 0.10',
            'ratio("node_load1", "node_num_cpu") > 1.5',
            "up == 0",
            "node_systemd_units_failed > 0",
            'ratio("x", "y") >= 100',
            'foo{job="node-exporter"} == 0',
            "metric_val != 0.0",
        ],
    )
    def test_valid(self, expr: str) -> None:
        parsed = validate_expr(expr)
        assert parsed.raw == expr.strip()
        assert parsed.rhs is not None

    def test_ratio_kind(self) -> None:
        parsed = validate_expr('ratio("a", "b") < 0.10')
        assert parsed.operand_kind == "ratio"
        assert parsed.comparison == "<"
        assert parsed.rhs == Decimal("0.10")

    def test_metric_kind(self) -> None:
        parsed = validate_expr('up{job="node-exporter"} == 0')
        assert parsed.operand_kind == "metric"
        assert parsed.comparison == "=="

    @pytest.mark.parametrize(
        "expr",
        [
            "",
            "garbage",
            "ratio(a, b) < 0.1",  # series refs must be quoted
            'ratio("a") < 0.1',  # wrong arity
            'ratio("a", "b", "c") < 0.1',  # wrong arity
            "ratio(a, b)",  # missing comparison
            "up <",  # missing rhs
            "up == 0x",  # bad number
            "up op 0",  # bad operator
            "0.1 < up",  # rhs on the left
        ],
    )
    def test_invalid(self, expr: str) -> None:
        with pytest.raises(RuleValidationError):
            validate_expr(expr)

    def test_bad_labels_rejected(self) -> None:
        with pytest.raises(RuleValidationError):
            validate_expr('up{job = node-exporter} == 0')  # unquoted value


class TestLoadRules:
    def test_loads_real_rule_file(self) -> None:
        rules = load_rules(RULES_FILE)
        assert {r.name for r in rules} == {
            "disk-low-free-ratio",
            "high-node-load",
            "scrape-target-down",
            "systemd-unit-failed",
        }
        assert len(rules) == 4

    def test_rule_fields(self) -> None:
        rules = {r.name: r for r in load_rules(RULES_FILE)}
        disk = rules["disk-low-free-ratio"]
        assert disk.severity == "critical"
        assert disk.for_.seconds == 300
        assert disk.expr.operand_kind == "ratio"
        assert disk.summary

    def test_bad_expr_fails_validation(self) -> None:
        bad = (
            '[rule]\nname = "x"\nexpr = "garbage"\nfor = "5m"\n'
            'severity = "warning"\nsummary = "s"\n'
        )
        with pytest.raises(RuleValidationError):
            load_rules_from_text(bad)

    def test_missing_field_fails(self) -> None:
        bad = (
            '[rule]\nname = "x"\nexpr = "up == 0"\nseverity = "warning"\nsummary = "s"\n'
        )
        with pytest.raises(RuleValidationError):
            load_rules_from_text(bad)

    def test_bad_severity_fails(self) -> None:
        bad = (
            '[rule]\nname = "x"\nexpr = "up == 0"\nfor = "5m"\n'
            'severity = "bogus"\nsummary = "s"\n'
        )
        with pytest.raises(RuleValidationError):
            load_rules_from_text(bad)

    def test_bad_duration_fails(self) -> None:
        bad = (
            '[rule]\nname = "x"\nexpr = "up == 0"\nfor = "99x"\n'
            'severity = "warning"\nsummary = "s"\n'
        )
        with pytest.raises(RuleValidationError):
            load_rules_from_text(bad)

    def test_duplicate_names_fail(self) -> None:
        bad = (
            '[rule]\nname = "dup"\nexpr = "up == 0"\nfor = "5m"\n'
            'severity = "warning"\nsummary = "s"\n'
            '[rule]\nname = "dup"\nexpr = "up == 1"\nfor = "5m"\n'
            'severity = "warning"\nsummary = "s"\n'
        )
        with pytest.raises(RuleValidationError):
            load_rules_from_text(bad)

    def test_invalid_toml_fails(self) -> None:
        with pytest.raises(RuleValidationError):
            load_rules_from_text("this is = = not valid toml [[")
