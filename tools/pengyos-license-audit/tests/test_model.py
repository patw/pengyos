"""Tests for the data model: pins/policy loading and validation."""

from __future__ import annotations

import textwrap

import pytest

from pengyos_license_audit.errors import ManifestError, PolicyError
from pengyos_license_audit.model import load_pins, load_policy


def test_load_pins_returns_typed_pins(pins_path):
    pins = load_pins(pins_path)
    assert len(pins) == 7
    glibc = next(p for p in pins if p.id == "glibc")
    assert glibc.license == "LGPL-2.1-only"
    assert glibc.tier == "core"
    assert glibc.link == "dynamic"
    assert glibc.is_core is True
    assert glibc.links_static is False


def test_load_pins_defaults_tier_and_link():
    import tempfile
    from pathlib import Path

    from pengyos_license_audit.model import load_pins

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "pins.toml"
        p.write_text(
            textwrap.dedent(
                """
                [[component]]
                id = "bare"
                license = "MIT"
                """
            ).strip()
        )
        pins = load_pins(str(p))
    assert pins[0].tier == "leaf"
    assert pins[0].link == "dynamic"


def test_load_pins_rejects_missing_license(pins_path):
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.toml"
        p.write_text(
            textwrap.dedent(
                """
                [[component]]
                id = "nolic"
                """
            ).strip()
        )
        with pytest.raises(ManifestError, match="missing a string 'license'"):
            load_pins(str(p))


def test_load_pins_rejects_bad_tier():
    import tempfile
    from pathlib import Path

    from pengyos_license_audit.model import load_pins

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.toml"
        p.write_text(
            textwrap.dedent(
                """
                [[component]]
                id = "x"
                license = "MIT"
                tier = "kernel"
                """
            ).strip()
        )
        with pytest.raises(ManifestError, match="invalid tier"):
            load_pins(str(p))


def test_load_pins_rejects_duplicates():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "dup.toml"
        p.write_text(
            textwrap.dedent(
                """
                [[component]]
                id = "x"
                license = "MIT"
                [[component]]
                id = "x"
                license = "Apache-2.0"
                """
            ).strip()
        )
        with pytest.raises(ManifestError, match="duplicate component id"):
            load_pins(str(p))


def test_load_pins_rejects_empty_manifest():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "empty.toml"
        p.write_text("[other]\nx = 1\n")
        with pytest.raises(ManifestError, match="no 'component'"):
            load_pins(str(p))


def test_load_pins_missing_file():
    with pytest.raises(FileNotFoundError):
        load_pins("/nonexistent/pins.toml")


def test_load_policy_parses(policy_path):
    policy = load_policy(policy_path)
    assert "MIT" in policy.allowed_families
    assert policy.copyleft_in_core is False
    assert "firmware-blob" in policy.exceptions


def test_load_policy_defaults_copyleft_false():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "policy.toml"
        p.write_text('allowed_families = ["MIT"]\n')
        policy = load_policy(str(p))
    assert policy.copyleft_in_core is False
    assert policy.exceptions == []


def test_load_policy_rejects_bad_family_type():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad.toml"
        p.write_text("allowed_families = 5\n")
        with pytest.raises(PolicyError, match="allowed_families"):
            load_policy(str(p))
