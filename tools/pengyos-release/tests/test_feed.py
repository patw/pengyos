"""Tests for feed layout, persistence, and channel pointers."""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_release.errors import FeedError
from pengyos_release.feed import (
    channel_pointer_path,
    index_path,
    list_all,
    list_editions,
    list_versions,
    manifest_path,
    read_channel_pointer,
    read_index,
    read_manifest,
    write_atomic,
    write_channel_pointer,
    write_index,
    write_manifest,
)
from pengyos_release.manifest import Artifact, FeedIndex, ReleaseManifest


def test_write_atomic_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "file.bin"
    write_atomic(target, b"payload-bytes")
    assert target.read_bytes() == b"payload-bytes"


def test_write_atomic_is_atomic_no_partfile_left(tmp_path: Path) -> None:
    target = tmp_path / "file.json"
    write_atomic(target, b"{}")
    leftovers = list(tmp_path.glob(".tmp-*"))
    assert leftovers == []


def test_read_json_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(FeedError):
        read_manifest(tmp_path, "server", "1.0.0")


def test_read_json_invalid_raises(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(FeedError):
        read_index(tmp_path)


def test_write_and_read_manifest(tmp_path: Path) -> None:
    m = ReleaseManifest(edition="server", version="1.0.0", artifacts=[Artifact(name="rootfs.img", digest="abc", signature="sig")])
    write_manifest(tmp_path, m)
    assert manifest_path(tmp_path, "server", "1.0.0").is_file()
    got = read_manifest(tmp_path, "server", "1.0.0")
    assert got.edition == "server"
    assert got.version == "1.0.0"
    assert got.artifacts[0].name == "rootfs.img"


def test_list_editions_and_versions(tmp_path: Path) -> None:
    for ver in ("1.0.0", "1.2.0", "1.10.0"):
        write_manifest(tmp_path, ReleaseManifest(edition="server", version=ver))
    write_manifest(tmp_path, ReleaseManifest(edition="core", version="0.1.0"))

    assert list_editions(tmp_path) == ["core", "server"]
    assert list_versions(tmp_path, "server") == ["1.0.0", "1.2.0", "1.10.0"]


def test_list_all_orders_by_edition_then_version(tmp_path: Path) -> None:
    write_manifest(tmp_path, ReleaseManifest(edition="server", version="1.0.0"))
    write_manifest(tmp_path, ReleaseManifest(edition="core", version="0.1.0"))
    write_manifest(tmp_path, ReleaseManifest(edition="server", version="1.1.0"))
    assert list_all(tmp_path) == [("core", "0.1.0"), ("server", "1.0.0"), ("server", "1.1.0")]


def test_write_and_read_index(tmp_path: Path, signer) -> None:
    idx = FeedIndex(
        releases=[ReleaseManifest(edition="server", version="1.0.0")],
        channels={"stable": "1.0.0"},
    )
    idx.sign(signer)
    write_index(tmp_path, idx)
    assert index_path(tmp_path).is_file()
    got = read_index(tmp_path)
    assert got.channels == {"stable": "1.0.0"}
    assert got.releases[0].edition == "server"


def test_channel_pointer_roundtrip(tmp_path: Path) -> None:
    write_channel_pointer(tmp_path, "stable", "1.0.0")
    assert read_channel_pointer(tmp_path, "stable") == "1.0.0"
    assert channel_pointer_path(tmp_path, "stable").is_file()


def test_channel_pointer_missing_returns_none(tmp_path: Path) -> None:
    assert read_channel_pointer(tmp_path, "stable") is None


def test_index_signature_survives_roundtrip(tmp_path: Path, signer) -> None:
    idx = FeedIndex(
        releases=[ReleaseManifest(edition="server", version="1.0.0")],
        channels={"stable": "1.0.0"},
    )
    idx.sign(signer)
    write_index(tmp_path, idx)
    # A freshly read index (no in-memory _signature) must still verify, proving
    # the signature is persisted in index.json.
    read = read_index(tmp_path)
    assert read.verify(signer) is True
