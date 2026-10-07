"""Tests for the validate module: valid + invalid fixtures.

Each invalid case is its own failing fixture so the rule it guards stays
testable in isolation.
"""

from __future__ import annotations

import pytest

from pengyos_podman.model import Service
from pengyos_podman.validate import (
    SchemaError,
    validate,
    validate_structure,
    validate_strict,
)


def test_valid_service_has_no_errors(sample_service) -> None:
    assert validate(sample_service) == []


def test_missing_name_is_rejected() -> None:
    errors = validate(Service(name="", image="img"))
    assert any("name" in e for e in errors)


def test_missing_image_is_rejected() -> None:
    errors = validate(Service(name="x", image=""))
    assert any("image" in e for e in errors)


def test_bad_restart_value_is_rejected() -> None:
    errors = validate(Service(name="x", image="img", restart="sometimes"))
    assert any("restart" in e for e in errors)


@pytest.mark.parametrize(
    "restart",
    ["no", "on-success", "on-failure", "on-abnormal", "on-watchdog", "on-abort", "always"],
)
def test_valid_restart_values(restart: str) -> None:
    assert validate(Service(name="x", image="img", restart=restart)) == []


def test_validate_strict_raises_on_invalid() -> None:
    with pytest.raises(SchemaError) as excinfo:
        validate_strict(Service(name="", image=""))
    assert excinfo.value.errors


def test_validate_strict_passes_on_valid(sample_service) -> None:
    # Should not raise.
    validate_strict(sample_service)


# --- Structural schema rules (item 2): field shapes -------------------------
#
# Each invalid case is its own failing fixture so the rule it guards stays
# testable in isolation. These catch typos in the *shape* of a field
# (port/volume must be "<host>:<container>", env must be "key=value") before
# any unit is emitted.


def test_validate_structure_passes_on_valid_shapes() -> None:
    errors = validate_structure(
        Service(
            name="x",
            image="img",
            ports=["8080:80", "443"],  # host side optional
            volumes=["/data:/usr/share/data:Z", "$HOME/x:/y"],
            env={"FOO": "bar", "EMPTY": ""},
        )
    )
    assert errors == []


def test_empty_port_is_rejected() -> None:
    # An empty port string is not a valid "[host:]<container>" pair.
    errors = validate(Service(name="x", image="img", ports=[""]))
    assert any("port" in e for e in errors)


def test_port_trailing_colon_is_rejected() -> None:
    # A trailing colon with an empty container side is a typo.
    errors = validate(Service(name="x", image="img", ports=["8080:"]))
    assert any("port" in e for e in errors)


def test_bad_volume_format_is_rejected() -> None:
    # A single path with no container side is not a valid bind mount.
    errors = validate(Service(name="x", image="img", volumes=["/data"]))
    assert any("volume" in e and "<host>:<container>" in e for e in errors)


def test_bad_volume_triple_colon_is_rejected() -> None:
    errors = validate(Service(name="x", image="img", volumes=["/a:/b:/c:/d"]))
    assert any("volume" in e for e in errors)


def test_bad_env_empty_key_is_rejected() -> None:
    # env is a map, so it is always "key=value" structurally; a malformed key
    # (empty here) is the only way to trip the structural check.
    errors = validate(Service(name="x", image="img", env={"": "bar"}))
    assert any("env" in e for e in errors)


def test_bad_env_nonidentifier_key_is_rejected() -> None:
    # A key that is not a POSIX identifier (starts with a digit) is malformed.
    errors = validate(Service(name="x", image="img", env={"1BAD": "v"}))
    assert any("env" in e for e in errors)


def test_multiple_structural_errors_reported_at_once() -> None:
    # A bad port (trailing colon) AND a bad volume (no container side) -> both
    # structural errors reported at once.
    errors = validate(
        Service(name="x", image="img", ports=["8080:"], volumes=["/data"])
    )
    assert len(errors) == 2
    assert any("port" in e for e in errors)
    assert any("volume" in e for e in errors)


def test_full_validate_combines_structural_and_required() -> None:
    # Missing name AND a bad port (trailing colon) -> both reported.
    errors = validate(Service(name="", image="img", ports=["8080:"]))
    assert any("name" in e for e in errors)
    assert any("port" in e for e in errors)
