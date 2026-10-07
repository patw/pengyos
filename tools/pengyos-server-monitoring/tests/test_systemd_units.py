"""Tests for hardened exporter systemd units (task: exporter units)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_monitoring.systemd_units import (
    UnitValidationError,
    parse_unit,
    validate_unit,
    validate_unit_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORTERS_DIR = REPO_ROOT / "exporters"


@pytest.mark.parametrize(
    "filename",
    ["node-exporter.service", "thewatcher.service"],
)
def test_committed_units_have_required_sections_and_hardening(filename: str) -> None:
    unit = validate_unit_file(EXPORTERS_DIR / filename)
    assert unit.has_section("Unit")
    assert unit.has_section("Service")
    assert unit.has_section("Install")
    # ExecStart present and absolute.
    exec_start = unit.get("Service", "ExecStart")
    assert exec_start and exec_start.startswith("/")


@pytest.mark.parametrize(
    "filename",
    ["node-exporter.service", "thewatcher.service"],
)
def test_committed_units_have_all_hardening_directives(filename: str) -> None:
    from pengyos_monitoring.systemd_units import HARDENING_KEYS

    unit = parse_unit((EXPORTERS_DIR / filename).read_text(encoding="utf-8"))
    problems = validate_unit(unit)
    assert problems == [], problems


def test_parse_unit_sections_and_keys() -> None:
    text = "[Service]\nType=simple\nExecStart=/bin/true\n"
    unit = parse_unit(text)
    assert list(unit.sections) == ["Service"]
    assert unit.get("Service", "ExecStart") == "/bin/true"
    assert unit.get("Service", "Type") == "simple"


def test_missing_section_fails() -> None:
    unit = parse_unit("[Service]\nExecStart=/bin/true\n")
    problems = validate_unit(unit)
    assert any("[Unit]" in p for p in problems)
    assert any("[Install]" in p for p in problems)


def test_missing_execstart_fails() -> None:
    unit = parse_unit("[Unit]\nDescription=x\n[Service]\nType=simple\n[Install]\n")
    problems = validate_unit(unit)
    assert any("ExecStart" in p for p in problems)


def test_missing_hardening_fails() -> None:
    minimal = (
        "[Unit]\nDescription=x\n[Service]\nExecStart=/bin/true\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    unit = parse_unit(minimal)
    problems = validate_unit(unit)
    assert any("NoNewPrivileges" in p for p in problems)
    assert any("ProtectSystem" in p for p in problems)


def test_protectsystem_too_weak_fails() -> None:
    weak = (
        "[Unit]\nDescription=x\n[Service]\n"
        "ExecStart=/bin/true\nNoNewPrivileges=true\n"
        "ProtectSystem=tmp\nProtectHome=true\nPrivateTmp=true\n"
        "ProtectKernelTunables=true\nProtectControlGroups=true\n"
        "RestrictAddressFamilies=af/inet\nRestrictNamespaces=true\n"
        "LockPersonality=true\nRestrictSUIDSGID=true\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )
    unit = parse_unit(weak)
    problems = validate_unit(unit)
    assert any("ProtectSystem" in p and "not sufficiently strict" in p for p in problems)


def test_malformed_line_raises() -> None:
    with pytest.raises(UnitValidationError):
        parse_unit("ExecStart=/bin/true\n")  # key outside any section


def test_duplicate_key_raises() -> None:
    with pytest.raises(UnitValidationError):
        parse_unit("[Service]\nExecStart=/bin/true\nExecStart=/bin/false\n")


def test_comment_and_blank_lines_ignored() -> None:
    text = (
        "# a comment\n\n[Service]\n; another\nExecStart=/bin/true\n"
    )
    unit = parse_unit(text)
    assert unit.get("Service", "ExecStart") == "/bin/true"
