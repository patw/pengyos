"""Tests for the policy schema: ``Policy`` fields, helpers, and ``load_policy``.

These pin the schema defined by the "Define the policy schema" subtask:
allowed families, core-vs-leaf copyleft rule, and documented exceptions.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pengyos_license_audit.errors import PolicyError
from pengyos_license_audit.model import Policy, load_policy


def test_load_policy_parses_from_fixture(policy_path):
    policy = load_policy(policy_path)
    # Permissive-preferred families are declared...
    assert "MIT" in policy.allowed_families
    assert "Apache-2.0" in policy.allowed_families
    # ...copyleft in core is flagged (not allowed)...
    assert policy.copyleft_in_core is False
    # ...and one documented exception exists.
    assert "firmware-blob" in policy.exceptions


def test_policy_defaults():
    policy = Policy()
    assert policy.allowed_families == []
    assert policy.copyleft_in_core is False
    assert policy.exceptions == []


def test_policy_is_exception():
    policy = Policy(allowed_families=["MIT"], exceptions=["firmware-blob"])
    assert policy.is_exception("firmware-blob") is True
    assert policy.is_exception("glibc") is False
    # The frozenset helper exposes the same membership.
    assert policy.exception_ids == frozenset({"firmware-blob"})


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "policy.toml"
    p.write_text(textwrap.dedent(body).strip())
    return str(p)


def test_load_policy_copyleft_must_be_bool(tmp_path):
    p = _write(tmp_path, "allowed_families = ['MIT']\ncopyleft_in_core = 1\n")
    with pytest.raises(PolicyError, match="copyleft_in_core"):
        load_policy(p)


def test_load_policy_rejects_non_list_families(tmp_path):
    p = _write(tmp_path, "allowed_families = 5\n")
    with pytest.raises(PolicyError, match="allowed_families"):
        load_policy(p)


def test_load_policy_rejects_non_string_family(tmp_path):
    p = _write(tmp_path, "allowed_families = ['MIT', 2]\n")
    with pytest.raises(PolicyError, match="allowed_families"):
        load_policy(p)


def test_load_policy_rejects_empty_family(tmp_path):
    p = _write(tmp_path, "allowed_families = ['MIT', '']\n")
    with pytest.raises(PolicyError, match="empty string"):
        load_policy(p)


def test_load_policy_rejects_duplicate_exceptions(tmp_path):
    p = _write(tmp_path, "exceptions = ['a', 'a']\n")
    with pytest.raises(PolicyError, match="duplicate"):
        load_policy(p)


def test_load_policy_repeats_duplicate_families(tmp_path):
    p = _write(tmp_path, "allowed_families = ['MIT', 'MIT']\n")
    with pytest.raises(PolicyError, match="duplicate"):
        load_policy(p)


def test_load_policy_allows_empty_allowed_families(tmp_path):
    # A policy may declare no outright-allowed families (everything flagged
    # unless copyleft_in_core or an exception applies).
    p = _write(tmp_path, "copyleft_in_core = true\n")
    policy = load_policy(p)
    assert policy.allowed_families == []
    assert policy.copyleft_in_core is True
    assert policy.exceptions == []


def test_load_policy_trims_values(tmp_path):
    p = _write(tmp_path, "allowed_families = ['  MIT  ', 'ISC']\n")
    policy = load_policy(p)
    assert policy.allowed_families == ["MIT", "ISC"]


def test_load_policy_missing_file():
    with pytest.raises(FileNotFoundError):
        load_policy("/nonexistent/policy.toml")
