"""Tests for the release metadata + feed-index schema."""

from __future__ import annotations

import json

import pytest

from pengyos_release.errors import ManifestError
from pengyos_release.manifest import (
    Artifact,
    FeedIndex,
    ReleaseManifest,
    canonicalise,
    parse_version,
    to_canonical_bytes,
)


def test_parse_version_basic() -> None:
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.10.0") == (0, 10, 0)


def test_parse_version_rejects_non_numeric() -> None:
    with pytest.raises(ManifestError):
        parse_version("1.2.beta")


def test_parse_version_requires_dotted() -> None:
    with pytest.raises(ManifestError):
        parse_version("1")


def test_parse_version_rejects_empty() -> None:
    with pytest.raises(ManifestError):
        parse_version("")


def test_canonicalise_sorts_keys_recursively() -> None:
    obj = {"b": {"d": 1, "c": 2}, "a": [3, 1, 2]}
    canon = canonicalise(obj)
    assert list(canon.keys()) == ["a", "b"]
    assert list(canon["b"].keys()) == ["c", "d"]
    assert canon["a"] == [3, 1, 2]  # lists are NOT reordered


def test_to_canonical_bytes_is_deterministic() -> None:
    obj = {"z": 1, "a": 2, "m": {"y": 9, "x": 8}}
    assert to_canonical_bytes(obj) == to_canonical_bytes({"a": 2, "z": 1, "m": {"x": 8, "y": 9}})


def test_manifest_dict_has_unique_keys() -> None:
    m = ReleaseManifest(edition="server", version="1.0.0", notes="hi")
    d = m.to_dict()
    # edition + version must both be present exactly once.
    assert d["edition"] == "server"
    assert d["version"] == "1.0.0"
    assert d["notes"] == "hi"
    assert "manifest" in d


def test_manifest_from_dict_roundtrip() -> None:
    m = ReleaseManifest(
        edition="core",
        version="2.1.0",
        artifacts=[Artifact(name="rootfs.img", digest="abc", signature="sig")],
        notes="release notes",
    )
    again = ReleaseManifest.from_dict(m.to_dict())
    assert again.edition == "core"
    assert again.version == "2.1.0"
    assert again.artifacts[0].name == "rootfs.img"
    assert again.artifacts[0].digest == "abc"
    assert again.notes == "release notes"


def test_manifest_from_dict_rejects_unknown_schema() -> None:
    with pytest.raises(ManifestError):
        ReleaseManifest.from_dict({"schema": "wrong", "edition": "x", "version": "1"})


def test_manifest_from_dict_missing_fields() -> None:
    with pytest.raises(ManifestError):
        ReleaseManifest.from_dict({"edition": "x"})


def test_manifest_sign_and_verify(signer) -> None:
    m = ReleaseManifest(edition="server", version="1.0.0")
    m.sign(signer)
    assert m.verify(signer) is True
    # Tampering with the payload must break verification.
    m.edition = "core"
    assert m.verify(signer) is False


def test_manifest_sign_is_keyed() -> None:
    # Two distinct keyed signers must not cross-verify.
    from pengyos_release.signing import HmacSigner

    m = ReleaseManifest(edition="server", version="1.0.0")
    m.sign(HmacSigner(b"key-a"))
    assert m.verify(HmacSigner(b"key-a")) is True
    assert m.verify(HmacSigner(b"key-b")) is False


def test_index_orders_releases_by_version() -> None:
    idx = FeedIndex(
        releases=[
            ReleaseManifest(edition="server", version="1.10.0"),
            ReleaseManifest(edition="server", version="1.2.0"),
        ],
        channels={"stable": "1.2.0"},
    )
    d = idx.to_dict()
    versions = [r["version"] for r in d["releases"]]
    assert versions == ["1.2.0", "1.10.0"]


def test_index_channels_sorted() -> None:
    idx = FeedIndex(channels={"testing": "2.0.0", "stable": "1.0.0"})
    assert list(idx.to_dict()["channels"].keys()) == ["stable", "testing"]


def test_index_sign_and_verify(signer) -> None:
    idx = FeedIndex(releases=[ReleaseManifest(edition="server", version="1.0.0")], channels={"stable": "1.0.0"})
    idx.sign(signer)
    assert idx.verify(signer) is True
    assert idx.signature  # non-empty
    # Tampering with the payload must break verification.
    idx.channels["stable"] = "9.9.9"
    assert idx.verify(signer) is False


def test_index_from_dict_roundtrip() -> None:
    idx = FeedIndex(
        releases=[ReleaseManifest(edition="core", version="1.0.0")],
        channels={"stable": "1.0.0", "testing": "2.0.0"},
    )
    again = FeedIndex.from_dict(idx.to_dict())
    assert again.channels == {"stable": "1.0.0", "testing": "2.0.0"}
    assert again.releases[0].edition == "core"


def test_canonical_bytes_is_valid_json() -> None:
    obj = {"a": 1, "b": [1, 2, 3]}
    parsed = json.loads(to_canonical_bytes(obj))
    assert parsed == obj
