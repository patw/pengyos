"""Tests for :mod:`pengyos_hwprofile_kernel.profiles`.

These lock in the "consume the hwprofile schema from committed fixtures;
validate required device fields; fail cleanly on a malformed profile" contract
. They drive the real
``pengyos_hwprofile.schema.validate`` through :func:`load_profile`, against the
committed fixture and inline malformed documents — no network, no root, no
hardware.
"""

from __future__ import annotations

import os

import pytest

from pengyos_hwprofile_kernel.profiles import ProfileError, load_profile

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str) -> str:
    return os.path.join(FIXTURES, name)


# ---------------------------------------------------------------------------
# Success: a committed fixture loads and validates.
# ---------------------------------------------------------------------------


def test_load_profile_valid_fixture_returns_document():
    doc = load_profile(_fixture("profile.toml"))
    # It is a plain dict (the hwprofile document shape).
    assert isinstance(doc, dict)
    # The two mandatory sections are present.
    assert "metadata" in doc
    assert "cpu" in doc


def test_load_profile_validates_required_device_fields():
    """A valid profile carries the required device fields the schema pins."""
    doc = load_profile(_fixture("profile.toml"))

    # metadata: hostname / kernel / tool_version are all required.
    for key in ("hostname", "kernel", "tool_version"):
        assert key in doc["metadata"], f"metadata.{key} is required"

    # cpu: vendor / model / cores / threads / mhz are all required.
    for key in ("vendor", "model", "cores", "threads", "mhz"):
        assert key in doc["cpu"], f"cpu.{key} is required"

    # The fixture is a real AMD laptop: the device classes are decodable.
    assert doc["cpu"]["vendor"] == "AuthenticAMD"
    assert len(doc["gpu"]) >= 1
    assert len(doc["storage"]) >= 1
    assert len(doc["network"]) >= 1


def test_load_profile_exposes_all_schema_sections():
    """The loaded document exposes every hwprofile schema section."""
    doc = load_profile(_fixture("profile.toml"))
    for section in (
        "metadata",
        "cpu",
        "memory",
        "gpu",
        "storage",
        "network",
        "peripherals",
    ):
        assert section in doc


# ---------------------------------------------------------------------------
# Failure: clean errors, never a traceback.
# ---------------------------------------------------------------------------


def test_load_profile_missing_file_raises_profile_error():
    with pytest.raises(ProfileError) as excinfo:
        load_profile(_fixture("does-not-exist.toml"))
    # The message names the offending path.
    assert "does-not-exist.toml" in str(excinfo.value)


def test_load_profile_malformed_toml_raises_profile_error():
    bad = _fixture("malformed.toml")
    with pytest.raises(ProfileError) as excinfo:
        load_profile(bad)
    # It is diagnosed as a TOML problem, not a schema problem.
    assert "not valid TOML" in str(excinfo.value)


def test_load_profile_missing_required_field_raises_profile_error():
    """A schema violation (missing required field) fails cleanly."""
    bad = _fixture("missing_cpu_field.toml")
    with pytest.raises(ProfileError) as excinfo:
        load_profile(bad)
    # The hwprofile path that failed is surfaced through the wrapper.
    assert "cpu" in str(excinfo.value)


def test_load_profile_wrong_type_raises_profile_error():
    """A wrong-typed field (e.g. cores as a string) fails cleanly."""
    bad = _fixture("wrong_type.toml")
    with pytest.raises(ProfileError) as excinfo:
        load_profile(bad)
    assert "expected int" in str(excinfo.value) or "expected" in str(excinfo.value)


def test_load_profile_unknown_section_raises_profile_error():
    """An unknown top-level section fails cleanly."""
    bad = _fixture("unknown_section.toml")
    with pytest.raises(ProfileError) as excinfo:
        load_profile(bad)
    assert "unknown top-level section" in str(excinfo.value)


def test_load_profile_inline_document(tmp_path):
    """A valid document written to a temp file loads and validates."""
    path = tmp_path / "inline.toml"
    path.write_text(
        "[metadata]\n"
        'hostname = "host"\n'
        'kernel = "6.8.0-pengyos"\n'
        'tool_version = "0.1.0"\n'
        "[cpu]\n"
        'vendor = "GenuineIntel"\n'
        'model = "Xeon"\n'
        "cores = 4\n"
        "threads = 8\n"
        "mhz = 2400.0\n",
        encoding="utf-8",
    )
    doc = load_profile(path)
    assert doc["cpu"]["vendor"] == "GenuineIntel"
    assert doc["cpu"]["cores"] == 4
