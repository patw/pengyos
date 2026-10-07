"""Tests for the first-class Channel model.

A channel is a named pointer from a promotion tier (``stable``, ``testing``,
...) to a single release version. These pin the name-validation rules, the
:class:`Channel` dataclass, and the index wiring.
"""

from __future__ import annotations

import pytest

from pengyos_release.errors import ChannelError  # noqa: F401 (used via pytest.raises)
from pengyos_release.manifest import (
    KNOWN_CHANNELS,
    Channel,
    FeedIndex,
    validate_channel_name,
)


@pytest.mark.parametrize("name", ["stable", "testing", "nightly", "a", "a-b-c", "edge2"])
def test_valid_channel_names(name: str) -> None:
    assert validate_channel_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["", "Stable", "1stable", "-stable", "stable_", "stable/x", "has space", "UPPER"],
)
def test_invalid_channel_names(name: str) -> None:
    with pytest.raises(ChannelError):
        validate_channel_name(name)


def test_channel_constructs_and_validates() -> None:
    c = Channel(name="stable", version="1.2.3")
    assert c.name == "stable"
    assert c.version == "1.2.3"
    assert c.to_dict() == {"name": "stable", "version": "1.2.3"}


@pytest.mark.parametrize("name", ["", "Bad", "1nope"])
def test_channel_rejects_bad_name(name: str) -> None:
    with pytest.raises(ChannelError):
        Channel(name=name, version="1.0.0")


@pytest.mark.parametrize("version", ["", "1", "1.2.beta", "x.y"])
def test_channel_rejects_bad_version(version: str) -> None:
    with pytest.raises(ChannelError):
        Channel(name="stable", version=version)


def test_channel_from_dict_roundtrip() -> None:
    c = Channel.from_dict({"name": "testing", "version": "2.0.0"})
    assert c.name == "testing"
    assert c.version == "2.0.0"


def test_channel_from_dict_missing_fields() -> None:
    with pytest.raises(ChannelError):
        Channel.from_dict({"name": "stable"})


def test_verify_pointer() -> None:
    c = Channel(name="stable", version="1.2.3")
    assert c.verify_pointer("1.2.3") is True
    assert c.verify_pointer("1.2.4") is False


def test_known_channels_contains_defaults() -> None:
    assert "stable" in KNOWN_CHANNELS
    assert "testing" in KNOWN_CHANNELS


def test_index_channel_accessor() -> None:
    idx = FeedIndex(channels={"stable": "1.0.0", "testing": "2.0.0"})
    ch = idx.channel("stable")
    assert ch is not None
    assert ch.name == "stable"
    assert ch.version == "1.0.0"


def test_index_channel_missing_returns_none() -> None:
    idx = FeedIndex(channels={"stable": "1.0.0"})
    assert idx.channel("testing") is None


def test_index_channel_rejects_bad_name() -> None:
    idx = FeedIndex(channels={"stable": "1.0.0"})
    with pytest.raises(ChannelError):
        idx.channel("Bad")


def test_index_set_channel_validates() -> None:
    idx = FeedIndex()
    idx.set_channel("stable", "1.0.0")
    assert idx.channels["stable"] == "1.0.0"
    with pytest.raises(ChannelError):
        idx.set_channel("Bad", "1.0.0")


def test_index_channels_as_list_ordered() -> None:
    idx = FeedIndex(channels={"testing": "2.0.0", "stable": "1.0.0"})
    names = [c.name for c in idx.channels_as_list()]
    assert names == ["stable", "testing"]
