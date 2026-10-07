"""Tests for the Core systemd unit set.

This suite is the objective function for the "Author a small, correct unit set
for Core" deliverable. It asserts that every committed unit:

- exists (and no stray units are present),
- parses as INI with case-sensitive keys (systemd semantics),
- uses correctly spelled ``[Section]`` names,
- carries ``ExecStart`` where a unit runs,
- carries ``WantedBy`` on any ``[Install]`` (WantedBy sanity),
- wires up socket activation correctly.

It also ships one failing fixture per rule (missing ``ExecStart``, a typo'd
section name, a missing ``WantedBy``) to prove the checks actually bite.

The validation helpers below are thin, string-view wrappers over
:mod:`pengyos_systemd_units.validator` — the single source of truth for the
validation rule table. They exist so the Core-set tests can assert on plain
messages without depending on the validator's ``Finding`` dataclass, and so a
regression in the package validator is caught here too.
"""

from pathlib import Path

import pytest

from pengyos_systemd_units import VALID_SECTION_NAMES, validator

ROOT = Path(__file__).resolve().parent.parent
UNITS_DIR = ROOT / "units"

# Only these suffixes are systemd unit files; everything else in units/
# (notably README.md) is documentation and must not be parsed as a unit.
UNIT_EXTS = {".service", ".target", ".socket"}

# The exact Core set for this increment.
EXPECTED_UNITS = {
    "pengyos-core.target",  # marker / synchronization point
    "sshd.socket",          # socket-activated OpenSSH listener
    "sshd.service",         # OpenSSH daemon (socket-activated)
    "pengy-agent.service",  # PengyOS agent
    "networkd.service",     # systemd-networkd (network configuration)
    "resolved.service",     # systemd-resolved (stub resolver / DNS)
    "journald.service",     # systemd-journald (logging)
}

# Canonical systemd section names (freedesktop.org unit file spec).
VALID_SECTION_NAMES = {
    "Unit",
    "Service",
    "Install",
    "Socket",
    "Timer",
    "Mount",
    "Automount",
    "Swap",
    "Path",
    "Slice",
    "Scope",
    "Target",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def parse_unit(path) -> validator.Unit:
    """Parse a unit file, preserving key case (systemd keys are case-sensitive).

    Delegates to :func:`pengyos_systemd_units.validator.parse_unit`, which raises
    :class:`configparser.Error` on malformed INI.
    """
    return validator.parse_unit(path)


def section_map(path) -> dict[str, dict[str, str]]:
    """Return ``{section_name: {key: value}}`` for a unit file."""
    unit = validator.parse_unit(path)
    return {name: dict(unit.sections[name]) for name in unit.sections}


def validate_unit(path) -> list[str]:
    """Return the error messages for ``path``.

    A thin, string view over
    :func:`pengyos_systemd_units.validator.validate_unit`: it surfaces only the
    error-level findings as plain messages, so the Core-set tests can assert on
    text without depending on the validator's ``Finding`` dataclass.
    """
    validation = validator.validate_unit(path)
    return [finding.message for finding in validation.errors]


# --------------------------------------------------------------------------- #
# Positive tests — the authored Core set
# --------------------------------------------------------------------------- #
def test_expected_unit_files_present() -> None:
    for unit in EXPECTED_UNITS:
        assert (UNITS_DIR / unit).is_file(), f"{unit} is missing"


def test_core_set_matches_expected_units() -> None:
    present = {p.name for p in UNITS_DIR.iterdir() if p.suffix in UNIT_EXTS}
    assert present == EXPECTED_UNITS, (
        f"unexpected unit files in units/: {present ^ EXPECTED_UNITS}"
    )


def test_all_units_parse() -> None:
    for unit in EXPECTED_UNITS:
        parse_unit(UNITS_DIR / unit)  # raises configparser.Error on malformed INI


def test_all_units_valid() -> None:
    for unit in EXPECTED_UNITS:
        errors = validate_unit(UNITS_DIR / unit)
        assert errors == [], f"{unit} has validation errors: {errors}"


def test_section_names_spelled_correctly() -> None:
    for unit in EXPECTED_UNITS:
        for sec in section_map(UNITS_DIR / unit):
            assert sec in VALID_SECTION_NAMES, f"bad section [{sec}] in {unit}"


def test_services_have_execstart() -> None:
    for unit in (
        "sshd.service",
        "pengy-agent.service",
        "networkd.service",
        "resolved.service",
        "journald.service",
    ):
        assert "ExecStart" in section_map(UNITS_DIR / unit)["Service"], unit


def test_install_sections_have_wantedby() -> None:
    for unit in EXPECTED_UNITS:
        if "Install" in section_map(UNITS_DIR / unit):
            assert "WantedBy" in section_map(UNITS_DIR / unit)["Install"], unit


def test_socket_has_listen_stream_and_service() -> None:
    sock = section_map(UNITS_DIR / "sshd.socket")["Socket"]
    assert "ListenStream" in sock
    assert sock["Service"] == "sshd.service"


def test_socket_activated_service_has_no_install() -> None:
    # sshd is socket-activated by sshd.socket, so it must not carry [Install].
    assert "Install" not in section_map(UNITS_DIR / "sshd.service")


def test_target_has_no_service_section() -> None:
    assert "Service" not in section_map(UNITS_DIR / "pengyos-core.target")


def test_keys_are_case_sensitive() -> None:
    svc = section_map(UNITS_DIR / "sshd.service")["Service"]
    # systemd keys are case-sensitive: 'ExecStart', not 'execstart'.
    assert "ExecStart" in svc
    assert "ExecStartPre" in svc
    assert "NoNewPrivileges" in svc


# The optional Core units authored in the follow-up increment. Each is a plain
# service (not socket-activated), so it must carry a [Service] with ExecStart and
# an [Install] with WantedBy, and it must ship the standard PengyOS hardening.
OPTIONAL_UNITS = {
    "networkd.service",
    "resolved.service",
    "journald.service",
}

# Hardening keys that every PengyOS service is expected to carry.
STANDARD_HARDENING = {"NoNewPrivileges", "ProtectSystem", "ProtectHome", "PrivateTmp"}


def test_optional_core_units_present() -> None:
    for unit in OPTIONAL_UNITS:
        assert (UNITS_DIR / unit).is_file(), f"{unit} is missing"


def test_optional_core_units_valid() -> None:
    for unit in OPTIONAL_UNITS:
        errors = validate_unit(UNITS_DIR / unit)
        assert errors == [], f"{unit} has validation errors: {errors}"


def test_optional_core_units_have_install_and_wantedby() -> None:
    for unit in OPTIONAL_UNITS:
        sections = section_map(UNITS_DIR / unit)
        assert "Install" in sections, f"{unit} is missing [Install]"
        assert "WantedBy" in sections["Install"], f"{unit} [Install] lacks WantedBy"


def test_optional_core_units_carry_standard_hardening() -> None:
    for unit in OPTIONAL_UNITS:
        svc = section_map(UNITS_DIR / unit)["Service"]
        assert STANDARD_HARDENING.issubset(svc.keys()), (
            f"{unit} is missing hardening keys: {STANDARD_HARDENING - svc.keys()}"
        )


# --------------------------------------------------------------------------- #
# Negative tests — one failing fixture per rule
# --------------------------------------------------------------------------- #
@pytest.fixture
def missing_execstart(tmp_path) -> Path:
    path = tmp_path / "bad.service"
    path.write_text(
        "[Unit]\n"
        "Description=missing ExecStart\n"
        "[Service]\n"
        "ExecStartPre=/bin/true\n",
        encoding="utf-8",
    )
    return path


def test_failing_fixture_missing_execstart(missing_execstart: Path) -> None:
    errors = validate_unit(missing_execstart)
    assert any("ExecStart" in e for e in errors), errors


@pytest.fixture
def bad_section_name(tmp_path) -> Path:
    path = tmp_path / "bad.target"
    path.write_text(
        "[Unit]\n"
        "Description=typo'd section\n"
        "[Servce]\n",  # typo: Servce instead of Service
        encoding="utf-8",
    )
    return path


def test_failing_fixture_bad_section_name(bad_section_name: Path) -> None:
    errors = validate_unit(bad_section_name)
    assert any("section name" in e for e in errors), errors


@pytest.fixture
def missing_wantedby(tmp_path) -> Path:
    path = tmp_path / "bad.service"
    path.write_text(
        "[Unit]\n"
        "Description=missing WantedBy\n"
        "[Service]\n"
        "ExecStart=/bin/true\n"
        "[Install]\n",  # [Install] with no WantedBy
        encoding="utf-8",
    )
    return path


def test_failing_fixture_missing_wantedby(missing_wantedby: Path) -> None:
    errors = validate_unit(missing_wantedby)
    assert any("WantedBy" in e for e in errors), errors
