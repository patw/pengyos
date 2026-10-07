"""Evaluator tests: failure fires, healthy stays silent (US-SRV-3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_monitoring.evaluator import Alert, evaluate
from pengyos_monitoring.rules import load_rules
from pengyos_monitoring.series import parse_series

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "fixtures" / "series"
RULES_FILE = REPO_ROOT / "rules" / "pengyos.toml"


def _series(text: str):
    return parse_series(text)


# --------------------------------------------------------------------------- #
# ratio() evaluation
# --------------------------------------------------------------------------- #
def test_ratio_fires_when_sustained_below() -> None:
    series = _series(
        'node_filesystem_free_bytes{instance="n"} 5 0\n'
        'node_filesystem_free_bytes{instance="n"} 5 60\n'
        'node_filesystem_free_bytes{instance="n"} 5 120\n'
        'node_filesystem_free_bytes{instance="n"} 5 180\n'
        'node_filesystem_free_bytes{instance="n"} 5 240\n'
        'node_filesystem_free_bytes{instance="n"} 5 300\n'
        'node_filesystem_size_bytes{instance="n"} 100 0\n'
        'node_filesystem_size_bytes{instance="n"} 100 60\n'
        'node_filesystem_size_bytes{instance="n"} 100 120\n'
        'node_filesystem_size_bytes{instance="n"} 100 180\n'
        'node_filesystem_size_bytes{instance="n"} 100 240\n'
        'node_filesystem_size_bytes{instance="n"} 100 300\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="disk"\nexpr=\'ratio("node_filesystem_free_bytes", '
        '"node_filesystem_size_bytes") < 0.10\'\nfor="5m"\n'
        'severity="critical"\nsummary="low"\n'
    )[0]
    alert = evaluate(rule, series)
    assert isinstance(alert, Alert)
    assert alert.rule_name == "disk"
    assert alert.value == pytest.approx(0.05)
    assert alert.timestamp == 300.0


def test_ratio_healthy_stays_silent() -> None:
    series = _series(
        'node_filesystem_free_bytes{instance="n"} 45 0\n'
        'node_filesystem_free_bytes{instance="n"} 45 60\n'
        'node_filesystem_size_bytes{instance="n"} 100 0\n'
        'node_filesystem_size_bytes{instance="n"} 100 60\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="disk"\nexpr=\'ratio("node_filesystem_free_bytes", '
        '"node_filesystem_size_bytes") < 0.10\'\nfor="5m"\n'
        'severity="critical"\nsummary="low"\n'
    )[0]
    assert evaluate(rule, series) is None


def test_ratio_transient_failure_is_not_sustained() -> None:
    series = _series(
        'node_filesystem_free_bytes{instance="n"} 90 0\n'
        'node_filesystem_free_bytes{instance="n"} 90 60\n'
        'node_filesystem_free_bytes{instance="n"} 5 120\n'
        'node_filesystem_size_bytes{instance="n"} 100 0\n'
        'node_filesystem_size_bytes{instance="n"} 100 60\n'
        'node_filesystem_size_bytes{instance="n"} 100 120\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="disk"\nexpr=\'ratio("node_filesystem_free_bytes", '
        '"node_filesystem_size_bytes") < 0.10\'\nfor="5m"\n'
        'severity="critical"\nsummary="low"\n'
    )[0]
    # Only the latest sample is below threshold -> not sustained for 5m.
    assert evaluate(rule, series) is None


def test_run_shorter_than_for_does_not_fire() -> None:
    # Two true samples 60s apart, but for=5m (300s) -> run too short.
    series = _series(
        'node_systemd_units_failed{instance="n"} 1 0\n'
        'node_systemd_units_failed{instance="n"} 1 60\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="failed"\nexpr="node_systemd_units_failed > 0"\n'
        'for="5m"\nseverity="critical"\nsummary="failed"\n'
    )[0]
    assert evaluate(rule, series) is None


def test_division_by_zero_is_not_evaluable() -> None:
    series = _series(
        'node_filesystem_free_bytes{instance="n"} 5 0\n'
        'node_filesystem_free_bytes{instance="n"} 5 60\n'
        'node_filesystem_size_bytes{instance="n"} 0 0\n'
        'node_filesystem_size_bytes{instance="n"} 0 60\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="disk"\nexpr=\'ratio("node_filesystem_free_bytes", '
        '"node_filesystem_size_bytes") < 0.10\'\nfor="5m"\n'
        'severity="critical"\nsummary="low"\n'
    )[0]
    assert evaluate(rule, series) is None


# --------------------------------------------------------------------------- #
# metric() evaluation
# --------------------------------------------------------------------------- #
def test_metric_fires_when_sustained() -> None:
    series = _series(
        'up{job="node-exporter",instance="n"} 0 0\n'
        'up{job="node-exporter",instance="n"} 0 60\n'
        'up{job="node-exporter",instance="n"} 0 120\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="down"\nexpr=\'up{job="node-exporter"} == 0\'\n'
        'for="2m"\nseverity="critical"\nsummary="down"\n'
    )[0]
    alert = evaluate(rule, series)
    assert isinstance(alert, Alert)
    assert alert.rule_name == "down"
    assert alert.value == 0.0


def test_metric_healthy_stays_silent() -> None:
    series = _series(
        'up{job="node-exporter",instance="n"} 1 0\n'
        'up{job="node-exporter",instance="n"} 1 60\n'
        'up{job="node-exporter",instance="n"} 1 120\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="down"\nexpr=\'up{job="node-exporter"} == 0\'\n'
        'for="2m"\nseverity="critical"\nsummary="down"\n'
    )[0]
    assert evaluate(rule, series) is None


def test_metric_missing_series_is_silent() -> None:
    rule = load_rules_from(
        '[[rule]]\nname="down"\nexpr=\'up{job="node-exporter"} == 0\'\n'
        'for="2m"\nseverity="critical"\nsummary="down"\n'
    )[0]
    assert evaluate(rule, _series("node_load1{instance=\"n\"} 9 0\n")) is None


# --------------------------------------------------------------------------- #
# sustained-for window semantics
# --------------------------------------------------------------------------- #
def test_single_sample_never_fires() -> None:
    series = _series('node_systemd_units_failed{instance="n"} 3 0\n')
    rule = load_rules_from(
        '[[rule]]\nname="failed"\nexpr="node_systemd_units_failed > 0"\n'
        'for="5m"\nseverity="critical"\nsummary="failed"\n'
    )[0]
    assert evaluate(rule, series) is None


def test_window_spanning_full_for_fires() -> None:
    # for=5m; two consecutive true samples spanning exactly 300s -> fires.
    series = _series(
        'node_systemd_units_failed{instance="n"} 1 0\n'
        'node_systemd_units_failed{instance="n"} 1 300\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="failed"\nexpr="node_systemd_units_failed > 0"\n'
        'for="5m"\nseverity="critical"\nsummary="failed"\n'
    )[0]
    alert = evaluate(rule, series)
    assert isinstance(alert, Alert)
    assert alert.timestamp == 300.0


def test_window_full_span_fires() -> None:
    series = _series(
        'node_systemd_units_failed{instance="n"} 1 0\n'
        'node_systemd_units_failed{instance="n"} 1 150\n'
        'node_systemd_units_failed{instance="n"} 1 300\n'
    )
    rule = load_rules_from(
        '[[rule]]\nname="failed"\nexpr="node_systemd_units_failed > 0"\n'
        'for="5m"\nseverity="critical"\nsummary="failed"\n'
    )[0]
    alert = evaluate(rule, series)
    assert isinstance(alert, Alert)


# --------------------------------------------------------------------------- #
# Fixture-driven: rules/pengyos.toml over committed series
# --------------------------------------------------------------------------- #
FAILURE_FIXTURES = {
    "failure-disk.prom": "disk-low-free-ratio",
    "failure-load.prom": "high-node-load",
    "failure-target-down.prom": "scrape-target-down",
    "failure-unit-failed.prom": "systemd-unit-failed",
    "failure-node-exporter.prom": "scrape-target-down",
}

HEALTHY_FIXTURES = {
    "healthy-disk.prom",
    "healthy-load.prom",
    "healthy-target-down.prom",
    "healthy-unit-failed.prom",
    "healthy-node-exporter.prom",
    "transient-disk.prom",
}


def load_rules_from(text: str):
    from pengyos_monitoring.rules import load_rules_from_text

    return load_rules_from_text(text)


@pytest.mark.parametrize("fixture,expected", sorted(FAILURE_FIXTURES.items()))
def test_failure_fixture_fires_expected_rule(fixture: str, expected: str) -> None:
    rules = load_rules(RULES_FILE)
    series = parse_series((FIXTURES / fixture).read_text(encoding="utf-8"))
    fired = [a for a in (evaluate(r, series) for r in rules) if a is not None]
    assert len(fired) == 1, f"{fixture}: expected exactly one alert, got {fired}"
    assert fired[0].rule_name == expected


@pytest.mark.parametrize("fixture", sorted(HEALTHY_FIXTURES))
def test_healthy_fixture_fires_nothing(fixture: str) -> None:
    rules = load_rules(RULES_FILE)
    series = parse_series((FIXTURES / fixture).read_text(encoding="utf-8"))
    fired = [a for a in (evaluate(r, series) for r in rules) if a is not None]
    assert fired == [], f"{fixture}: expected no alerts, got {fired}"


# --------------------------------------------------------------------------- #
# Induced-failure contract (spec: "each failure fixture fires its rule;
# healthy series fire nothing") — the acceptance criterion for US-SRV-3.
# --------------------------------------------------------------------------- #

# The three induced-failure scenarios named in the task, mapped to the fixture
# that encodes them. Every scenario must fire exactly one alert; the healthy
# mirror of each must stay silent.
INDUCED_FAILURE_SCENARIOS: dict[str, str] = {
    "failure-disk.prom": "disk filling (free-space ratio < 10%)",
    "failure-unit-failed.prom": "a systemd unit in failed state",
    "failure-target-down.prom": "scrape target down (up == 0)",
}


@pytest.mark.parametrize("fixture,scenario", sorted(INDUCED_FAILURE_SCENARIOS.items()))
def test_induced_failure_fixture_fires(fixture: str, scenario: str) -> None:
    """Each named induced-failure scenario fires exactly one alert."""
    rules = load_rules(RULES_FILE)
    series = parse_series((FIXTURES / fixture).read_text(encoding="utf-8"))
    fired = [a for a in (evaluate(r, series) for r in rules) if a is not None]
    assert len(fired) == 1, f"{fixture} ({scenario}): expected one alert, got {fired}"
    assert fired[0].rule_name
    assert fired[0].severity in ("info", "warning", "critical")


def test_every_induced_failure_fixture_has_a_healthy_mirror() -> None:
    """Every induced-failure fixture has a healthy counterpart that stays silent."""
    failure_names = set(INDUCED_FAILURE_SCENARIOS) | {
        "failure-load.prom",
        "failure-node-exporter.prom",
    }
    for failure in failure_names:
        mirror = failure.replace("failure-", "healthy-")
        assert mirror in HEALTHY_FIXTURES, (
            f"missing healthy mirror for {failure}: {mirror}"
        )


def test_induced_failure_contract_holds_for_all_fixtures() -> None:
    """The whole suite: induced failures fire, healthy series never do."""
    rules = load_rules(RULES_FILE)
    failure_fires = {
        f: [a.rule_name for a in (evaluate(r, parse_series((FIXTURES / f).read_text())) for r in rules) if a]
        for f in sorted(INDUCED_FAILURE_SCENARIOS)
    }
    healthy_silent = {
        f: [a.rule_name for a in (evaluate(r, parse_series((FIXTURES / f).read_text())) for r in rules) if a]
        for f in sorted(HEALTHY_FIXTURES)
    }
    for f, fired in failure_fires.items():
        assert len(fired) == 1, f"{f}: induced failure must fire exactly one alert, got {fired}"
    for f, fired in healthy_silent.items():
        assert fired == [], f"{f}: healthy series must stay silent, got {fired}"


def test_healthy_node_exporter_silence_all_rules() -> None:
    """A fully healthy scrape must not trip any committed rule."""
    rules = load_rules(RULES_FILE)
    series = parse_series((FIXTURES / "healthy-node-exporter.prom").read_text())
    assert [r.name for r in rules]  # sanity: rules loaded
    assert [a for a in (evaluate(r, series) for r in rules) if a] == []
