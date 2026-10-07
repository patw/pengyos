"""Tests for the change schema (pengyos_tiers.change)."""

from __future__ import annotations

import pytest

from pengyos_tiers.change import Change
from pengyos_tiers.errors import ChangeError


def test_from_dict_valid_minimal():
    c = Change.from_dict({"action": "modify", "path": "/etc/foo"})
    assert c.action == "modify"
    assert c.path == "/etc/foo"
    assert c.target is None


def test_from_dict_valid_with_target():
    c = Change.from_dict(
        {"action": "install", "path": "/usr/bin/x", "target": "kernel"}
    )
    assert c.target == "kernel"


def test_from_dict_missing_action():
    with pytest.raises(ChangeError, match="action is required"):
        Change.from_dict({"path": "/etc/foo"})


def test_from_dict_bad_action():
    with pytest.raises(ChangeError, match="lowercase identifier"):
        Change.from_dict({"action": "Modify", "path": "/etc/foo"})


def test_from_dict_missing_path():
    with pytest.raises(ChangeError, match="path is required"):
        Change.from_dict({"action": "modify"})


def test_from_dict_path_with_whitespace():
    with pytest.raises(ChangeError, match="no whitespace"):
        Change.from_dict({"action": "modify", "path": "/etc/foo bar"})


def test_from_dict_bad_target():
    with pytest.raises(ChangeError, match="target must be"):
        Change.from_dict({"action": "modify", "path": "/etc/foo", "target": ""})


def test_from_dict_not_a_dict():
    with pytest.raises(ChangeError, match="must be a JSON object"):
        Change.from_dict(["modify"])


def test_to_dict_roundtrip():
    c = Change(action="delete", path="/etc/bar", target="svc")
    assert c.to_dict() == {
        "action": "delete",
        "path": "/etc/bar",
        "target": "svc",
    }
    assert Change.from_dict(c.to_dict()) == c
