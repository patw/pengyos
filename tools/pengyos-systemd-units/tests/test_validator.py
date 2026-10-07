"""Tests for the pengyos_systemd_units.validator package.

This is the objective function for the "Implement the validator" deliverable.
It exercises the public API (:func:`parse_unit`, :func:`validate_unit`,
:func:`validate_dir`, :func:`summary`, :func:`report`, :func:`to_json`), the
:class:`Unit`/:class:`UnitKind` model, and every rule in the rule table — with
one failing fixture per rule so a rule can never go untested.
"""

import configparser
import json
from pathlib import Path

import pytest

from pengyos_systemd_units import (
    Rule,
    Severity,
    UnitKind,
    validate_dir,
    validate_unit,
    validator,
)

ROOT = Path(__file__).resolve().parent.parent
UNITS_DIR = ROOT / "units"


# --------------------------------------------------------------------------- #
# UnitKind
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "suffix, expected",
    [
        (".service", UnitKind.SERVICE),
        (".target", UnitKind.TARGET),
        (".socket", UnitKind.SOCKET),
        (".Service", UnitKind.SERVICE),  # case-insensitive lookup
        (".TARGET", UnitKind.TARGET),
        (".md", UnitKind.UNKNOWN),
        ("", UnitKind.UNKNOWN),
    ],
)
def test_unitkind_from_suffix(suffix: str, expected: UnitKind) -> None:
    assert UnitKind.from_suffix(suffix) is expected


def test_unitkind_suffix_property() -> None:
    assert UnitKind.SERVICE.suffix == ".service"
    assert UnitKind.TARGET.suffix == ".target"


# --------------------------------------------------------------------------- #
# parse_unit
# --------------------------------------------------------------------------- #
def test_parse_unit_reads_sections_and_kind() -> None:
    unit = validator.parse_unit(UNITS_DIR / "sshd.service")
    assert unit.kind is UnitKind.SERVICE
    assert unit.name == "sshd.service"
    assert unit.has_section("Unit")
    assert unit.has_section("Service")
    assert not unit.has_section("Install")


def test_parse_unit_preserves_key_case() -> None:
    unit = validator.parse_unit(UNITS_DIR / "sshd.service")
    # systemd keys are case-sensitive: the keys are capitalised, not lowered.
    assert "ExecStart" in unit.keys("Service")
    assert "ExecStartPre" in unit.keys("Service")
    assert "no_new_privileges" not in unit.keys("Service")


def test_parse_unit_unknown_suffix_is_unknown_kind(tmp_path: Path) -> None:
    exotic = tmp_path / "weird.foo"
    exotic.write_text("[Unit]\nDescription=x\n", encoding="utf-8")
    assert validator.parse_unit(exotic).kind is UnitKind.UNKNOWN


def test_parse_unit_raises_on_malformed_ini(tmp_path: Path) -> None:
    bad = tmp_path / "noheader.service"
    bad.write_text("ExecStart=/bin/true\n", encoding="utf-8")  # no [Section]
    with pytest.raises(configparser.Error):
        validator.parse_unit(bad)


# --------------------------------------------------------------------------- #
# validate_unit — positive case
# --------------------------------------------------------------------------- #
def test_valid_units_all_pass() -> None:
    for validation in validate_dir(UNITS_DIR):
        assert validation.ok, f"{validation.unit.name} failed: {validation.errors}"


def test_validation_exposes_ok_and_errors() -> None:
    validation = validate_unit(UNITS_DIR / "pengy-agent.service")
    assert validation.unit.kind is UnitKind.SERVICE
    assert validation.ok
    assert validation.errors == []
    assert validation.warnings == []


# --------------------------------------------------------------------------- #
# validate_unit — one failing fixture per rule (table-driven)
# --------------------------------------------------------------------------- #
# Each entry: (rule, unit body, file suffix). The body is written verbatim as
# the unit file; the fixture is expected to trigger the named rule.
RULE_FIXTURES: list[tuple[Rule, str, str]] = [
    (Rule.REQUIRED_UNIT, "[Service]\nExecStart=/bin/true\n", ".service"),
    (Rule.SECTION_NAME, "[Unit]\nDescription=x\n[Badsec]\nKey=val\n", ".target"),
    (Rule.REQUIRED_SERVICE, "[Unit]\nDescription=x\n", ".service"),
    (Rule.EXEC_START, "[Unit]\nDescription=x\n[Service]\n", ".service"),
    (
        Rule.TARGET_NO_EXEC_START,
        "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/true\n",
        ".target",
    ),
    (
        Rule.SOCKET_LISTEN_STREAM,
        "[Unit]\nDescription=x\n[Socket]\nService=x.service\n",
        ".socket",
    ),
    (Rule.SOCKET_SERVICE, "[Unit]\nDescription=x\n[Socket]\nListenStream=22\n", ".socket"),
    (Rule.WANTEDBY, "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/true\n[Install]\n", ".service"),
]


@pytest.mark.parametrize(
    "rule, body, suffix",
    RULE_FIXTURES,
    ids=[rule.value for rule, _, _ in RULE_FIXTURES],
)
def test_each_rule_has_a_failing_fixture(
    tmp_path: Path, rule: Rule, body: str, suffix: str
) -> None:
    # pytest's built-in tmp_path gives each parametrised case its own dir; the
    # suffix drives the derived UnitKind, so the filename must match.
    path = tmp_path / f"unit{suffix}"
    path.write_text(body, encoding="utf-8")
    errors = validate_unit(path).errors
    assert any(f.rule is rule for f in errors), (
        f"rule {rule.value} was not triggered by its fixture"
    )


# --------------------------------------------------------------------------- #
# validate_unit — combined / multi-finding cases
# --------------------------------------------------------------------------- #
def test_multiple_errors_are_all_reported(tmp_path: Path) -> None:
    bad = tmp_path / "many.service"
    bad.write_text(
        "[Service]\n"  # no [Unit]
        "ExecStartPre=/bin/true\n",  # no ExecStart
        encoding="utf-8",
    )
    errors = validate_unit(bad).errors
    rules = {f.rule for f in errors}
    assert Rule.REQUIRED_UNIT in rules
    assert Rule.EXEC_START in rules


def test_install_without_wantedby_only_when_install_present(tmp_path: Path) -> None:
    # A service with no [Install] at all is fine (e.g. socket-activated units).
    clean = tmp_path / "sa.service"
    clean.write_text(
        "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/true\n",
        encoding="utf-8",
    )
    assert validate_unit(clean).ok


# --------------------------------------------------------------------------- #
# validate_dir
# --------------------------------------------------------------------------- #
def test_validate_dir_only_reads_unit_files() -> None:
    # units/ contains README.md alongside the units; it must be skipped.
    validations = validate_dir(UNITS_DIR)
    names = {v.unit.name for v in validations}
    assert names == {
        "journald.service",
        "networkd.service",
        "pengy-agent.service",
        "pengyos-core.target",
        "resolved.service",
        "sshd.service",
        "sshd.socket",
    }
    assert all(v.ok for v in validations)


def test_validate_dir_is_sorted_by_name() -> None:
    names = [v.unit.name for v in validate_dir(UNITS_DIR)]
    assert names == sorted(names)


def test_validate_dir_skips_non_units(tmp_path: Path) -> None:
    (tmp_path / "a.service").write_text(
        "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/true\n[Install]\nWantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.md").write_text("# notes\n", encoding="utf-8")
    (tmp_path / "config.ini").write_text("a=b\n", encoding="utf-8")
    names = {v.unit.name for v in validate_dir(tmp_path)}
    assert names == {"a.service"}


# --------------------------------------------------------------------------- #
# summary / report / to_json
# --------------------------------------------------------------------------- #
def test_summary_counts_pass_and_fail(tmp_path: Path) -> None:
    (tmp_path / "ok.service").write_text(
        "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/true\n[Install]\nWantedBy=multi-user.target\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.service").write_text(
        "[Service]\nExecStartPre=/bin/true\n",
        encoding="utf-8",
    )
    validations = validate_dir(tmp_path)
    summary = validator.summary(validations)
    assert summary == {"units": 2, "passed": 1, "errored": 1}


def test_report_lists_failures(tmp_path: Path) -> None:
    (tmp_path / "bad.service").write_text(
        "[Service]\nExecStartPre=/bin/true\n",
        encoding="utf-8",
    )
    report = validator.report(validate_dir(tmp_path))
    assert "bad.service" in report
    assert "missing required [Unit]" in report


def test_to_json_is_valid_and_structured(tmp_path: Path) -> None:
    (tmp_path / "bad.service").write_text(
        "[Service]\nExecStartPre=/bin/true\n",
        encoding="utf-8",
    )
    payload = json.loads(validator.to_json(validate_dir(tmp_path)))
    assert len(payload) == 1
    entry = payload[0]
    assert entry["unit"] == "bad.service"
    assert entry["kind"] == "service"
    assert entry["ok"] is False
    assert entry["findings"][0]["rule"] == "required[Unit]"
    assert entry["findings"][0]["severity"] == "error"


# --------------------------------------------------------------------------- #
# Rule table shape
# --------------------------------------------------------------------------- #
def test_rule_table_is_non_empty_and_well_formed() -> None:
    assert validator.RULES
    rule_members = {r.value for r in Rule}
    for spec in validator.RULES:
        assert isinstance(spec.severity, Severity)
        assert spec.rule.value in rule_members
        if spec.applies_to is not None:
            for kind in spec.applies_to:
                assert isinstance(kind, UnitKind)


def test_known_rules_are_registered() -> None:
    registered = {spec.rule for spec in validator.RULES}
    # Every rule the content engine references must be in the table.
    for rule in [
        Rule.REQUIRED_UNIT,
        Rule.SECTION_NAME,
        Rule.REQUIRED_SERVICE,
        Rule.EXEC_START,
        Rule.TARGET_NO_EXEC_START,
        Rule.SOCKET_LISTEN_STREAM,
        Rule.SOCKET_SERVICE,
        Rule.WANTEDBY,
    ]:
        assert rule in registered


def test_section_name_rule_applies_to_all_kinds() -> None:
    spec = next(s for s in validator.RULES if s.rule is Rule.SECTION_NAME)
    assert spec.applies_to is None  # applies to every unit kind


def test_finding_as_dict_roundtrip() -> None:
    finding = validator.Finding(
        rule=Rule.EXEC_START,
        severity=Severity.ERROR,
        message="x.service: [Service] is missing ExecStart",
        section="Service",
        key="ExecStart",
    )
    data = finding.as_dict()
    assert data == {
        "rule": "execStart",
        "severity": "error",
        "message": data["message"],
        "section": "Service",
        "key": "ExecStart",
    }


def test_severity_error_code() -> None:
    assert Severity.ERROR.code == 1
    assert Severity.WARNING.code == 0
