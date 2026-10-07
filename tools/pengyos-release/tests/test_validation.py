"""Tests for structural validation of the committed, validated data.

Covers :meth:`Artifact.validate`, :meth:`ReleaseManifest.validate`,
:meth:`FeedIndex.validate`, and the on-disk :func:`validate_feed` walker. The
goal: a well-formed feed validates, and each mutation the spec calls out
(unsigned entry, bad digest, bad channel, missing index) is caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_release.errors import ChannelError, FeedError, ManifestError
from pengyos_release.feed import (
    read_index,
    write_channel_pointer,
    write_index,
    write_manifest,
    validate_feed,
)
from pengyos_release.manifest import (
    Artifact,
    FeedIndex,
    ReleaseManifest,
    sha256_bytes,
)

ROOTFS_DIGEST = sha256_bytes(b"PENGYOS-ROOTFS\n")


def _signed_manifest(edition: str = "server", version: str = "1.0.0", signer=None) -> ReleaseManifest:
    m = ReleaseManifest(
        edition=edition,
        version=version,
        artifacts=[Artifact(name="rootfs.img", digest=ROOTFS_DIGEST, signature="sig")],
        notes="release notes",
    )
    if signer is not None:
        m.sign(signer)
    return m


# --- Artifact.validate ------------------------------------------------------

def test_artifact_validate_ok() -> None:
    a = Artifact(name="rootfs.img", digest=ROOTFS_DIGEST, signature="sig")
    assert a.validate() is a


def test_artifact_validate_empty_name() -> None:
    with pytest.raises(ManifestError):
        Artifact(name="", digest=ROOTFS_DIGEST, signature="sig").validate()


def test_artifact_validate_bad_algorithm() -> None:
    with pytest.raises(ManifestError):
        Artifact(name="x", algorithm="md5", digest=ROOTFS_DIGEST, signature="sig").validate()


def test_artifact_validate_bad_digest() -> None:
    with pytest.raises(ManifestError):
        Artifact(name="x", digest="not-a-digest", signature="sig").validate()


def test_artifact_validate_missing_signature() -> None:
    with pytest.raises(ManifestError):
        Artifact(name="x", digest=ROOTFS_DIGEST, signature="").validate()


# --- ReleaseManifest.validate ----------------------------------------------

def test_manifest_validate_ok(signer) -> None:
    m = _signed_manifest(signer=signer)
    assert m.validate() is m


def test_manifest_validate_empty_edition(signer) -> None:
    m = _signed_manifest(edition="", signer=signer)
    with pytest.raises(ManifestError):
        m.validate()


def test_manifest_validate_bad_version(signer) -> None:
    m = _signed_manifest(version="nope", signer=signer)
    with pytest.raises(ManifestError):
        m.validate()


def test_manifest_validate_no_artifacts(signer) -> None:
    m = ReleaseManifest(edition="server", version="1.0.0", artifacts=[], notes="")
    m.sign(signer)
    with pytest.raises(ManifestError):
        m.validate()


def test_manifest_validate_bad_artifact_digest(signer) -> None:
    m = ReleaseManifest(
        edition="server",
        version="1.0.0",
        artifacts=[Artifact(name="rootfs.img", digest="bad", signature="sig")],
    )
    m.sign(signer)
    with pytest.raises(ManifestError):
        m.validate()


def test_manifest_validate_missing_signature() -> None:
    m = _signed_manifest()  # not signed
    with pytest.raises(ManifestError):
        m.validate()


# --- FeedIndex.validate -----------------------------------------------------

def test_index_validate_ok(signer) -> None:
    m = _signed_manifest(signer=signer)
    idx = FeedIndex(releases=[m], channels={"stable": "1.0.0", "testing": "1.0.0"})
    idx.sign(signer)
    assert idx.validate() is idx


def test_index_validate_bad_channel_name(signer) -> None:
    m = _signed_manifest(signer=signer)
    idx = FeedIndex(releases=[m], channels={"Bad": "1.0.0"})
    idx.sign(signer)
    with pytest.raises(ChannelError):
        idx.validate()


def test_index_validate_bad_channel_version(signer) -> None:
    m = _signed_manifest(signer=signer)
    idx = FeedIndex(releases=[m], channels={"stable": "nope"})
    idx.sign(signer)
    with pytest.raises(ManifestError):
        idx.validate()


def test_index_validate_bad_release(signer) -> None:
    bad = ReleaseManifest(edition="server", version="1.0.0", artifacts=[], notes="")
    bad.sign(signer)
    idx = FeedIndex(releases=[bad], channels={"stable": "1.0.0"})
    idx.sign(signer)
    with pytest.raises(ManifestError):
        idx.validate()


# --- validate_feed (on-disk) ------------------------------------------------

def _publish_feed(feed: Path, signer) -> None:
    """Lay out a well-formed feed on disk (mimics publish, without the CLI)."""
    m = _signed_manifest(signer=signer)
    write_manifest(feed, m)
    idx = FeedIndex(releases=[m], channels={"stable": "1.0.0", "testing": "1.0.0"})
    idx.sign(signer)
    write_index(feed, idx)
    write_channel_pointer(feed, "stable", "1.0.0")
    write_channel_pointer(feed, "testing", "1.0.0")


def test_validate_feed_ok(tmp_path, signer) -> None:
    _publish_feed(tmp_path, signer)
    res = validate_feed(tmp_path)
    assert res.ok is True
    assert res.manifests_validated == 1
    assert res.channels_validated == 2
    assert res.index_valid is True
    assert res.errors == []


def test_validate_feed_requires_directory(tmp_path) -> None:
    with pytest.raises(FeedError):
        validate_feed(tmp_path / "does-not-exist")


def test_validate_feed_missing_index(tmp_path, signer) -> None:
    # A manifest exists but no signed index was written.
    write_manifest(tmp_path, _signed_manifest(signer=signer))
    res = validate_feed(tmp_path)
    assert res.ok is False
    assert any("index" in e for e in res.errors)


def test_validate_feed_catches_unsigned_manifest(tmp_path) -> None:
    # Unsigned manifest -> structural validation fails (no signature present).
    write_manifest(tmp_path, _signed_manifest())  # not signed
    res = validate_feed(tmp_path)
    # also no index, so both errors are present
    assert res.ok is False
    assert any("manifest signature" in e or "no signature" in e for e in res.errors)


def test_validate_feed_catches_bad_digest(tmp_path, signer) -> None:
    bad = ReleaseManifest(
        edition="server",
        version="1.0.0",
        artifacts=[Artifact(name="rootfs.img", digest="deadbeef", signature="sig")],
    )
    bad.sign(signer)
    write_manifest(tmp_path, bad)
    res = validate_feed(tmp_path)
    assert res.ok is False
    assert any("digest" in e for e in res.errors)


def test_validate_feed_catches_bad_channel(tmp_path, signer) -> None:
    _publish_feed(tmp_path, signer)
    # Corrupt the index to point a channel at an invalid name.
    idx = read_index(tmp_path)
    idx.channels["Bad"] = "1.0.0"
    idx.sign(signer)
    write_index(tmp_path, idx)
    res = validate_feed(tmp_path)
    assert res.ok is False
    assert any("index" in e and "Bad" in e for e in res.errors)


def test_validate_feed_check_signatures_ok(tmp_path, signer) -> None:
    _publish_feed(tmp_path, signer)
    res = validate_feed(tmp_path, check_signatures=True, signer=signer)
    assert res.ok is True
    assert res.index_valid is True


def test_validate_feed_check_signatures_wrong_signer(tmp_path) -> None:
    # StubSigner is not keyed (two instances sign identically), so use a keyed
    # HmacSigner to prove a wrong key is rejected.
    from pengyos_release.signing import HmacSigner

    signing_key = HmacSigner(b"signing-key")
    _publish_feed(tmp_path, signing_key)
    res = validate_feed(tmp_path, check_signatures=True, signer=HmacSigner(b"tampered-key"))
    assert res.ok is False
    assert any("signature did not verify" in e for e in res.errors)


def test_validate_feed_reports_all_problems(tmp_path, signer) -> None:
    # A valid signed manifest + a bad-digest manifest, and no index: every
    # problem is collected, not just the first.
    write_manifest(tmp_path, _signed_manifest(signer=signer))
    bad = ReleaseManifest(
        edition="core",
        version="2.0.0",
        artifacts=[Artifact(name="rootfs.img", digest="deadbeef", signature="sig")],
    )
    bad.sign(signer)
    write_manifest(tmp_path, bad)
    # No index is written, so validation must report both the bad digest and
    # the missing index (all problems, not just the first).
    res = validate_feed(tmp_path)
    assert any("core/2.0.0" in e for e in res.errors)
    assert any("digest" in e for e in res.errors)
    assert any("index" in e for e in res.errors)


def test_validate_feed_signer_required_without_signer(tmp_path) -> None:
    with pytest.raises(FeedError):
        validate_feed(tmp_path, check_signatures=True)
