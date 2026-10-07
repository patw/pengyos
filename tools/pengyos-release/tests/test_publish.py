"""Tests for the ``publish`` handler: collect, compress, sign, and lay out.

These exercise the real publish path end to end against the committed fake
build-output fixture (``build_output`` in conftest), asserting the published
layout, deterministic compression, real sha256 digests over stored files,
per-artifact + manifest signatures, and index/channel wiring.
"""

from __future__ import annotations

import gzip
import hashlib
import json

import pytest

from pengyos_release import ARTIFACTS, publish
from pengyos_release.errors import BuildOutputError
from pengyos_release.feed import (
    index_path,
    manifest_path,
    read_index,
    read_manifest,
    validate_feed,
    version_dir,
)
from pengyos_release.manifest import FeedIndex, parse_version
from pengyos_release.signing import HmacSigner

# The committed fixture's artifact contents (see tests/fixtures/sample-build).
# These are the *source* bytes publish reads; the published rootfs decompresses
# back to exactly these, proving gzip round-trip fidelity.
FIXTURE_ROOTFS = (
    __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "sample-build" / "images" / "rootfs.img"
).read_bytes()


def _digest_of(feed, edition, version, name: str) -> str:
    """Return the recorded digest for ``name`` in a published manifest."""
    manifest = read_manifest(feed, edition, version)
    for artifact in manifest.artifacts:
        if artifact.name == name:
            return artifact.digest
    raise AssertionError(f"{name!r} not in manifest for {edition}/{version}")


def _stored_bytes(feed, edition, version, name: str) -> bytes:
    return version_dir(feed, edition, version) / name


# --- layout ---------------------------------------------------------------

def test_publish_lays_out_version_dir(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)

    vdir = version_dir(feed, "server", "1.0.0")
    assert vdir.is_dir()
    for name in ARTIFACTS:
        assert (vdir / name).is_file(), f"expected published artifact {name}"
    assert manifest_path(feed, "server", "1.0.0").is_file()


def test_publish_artifact_names_match_artifact_spec(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    manifest = publish(build_output, "core", "1.2.3", feed)
    assert {a.name for a in manifest.artifacts} == set(ARTIFACTS)
    assert "rootfs.img.gz" in ARTIFACTS  # rootfs is compressed on publish


# --- deterministic compression -------------------------------------------

def test_publish_rootfs_is_gzip(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    raw = _stored_bytes(feed, "server", "1.0.0", "rootfs.img.gz").read_bytes()
    # A gzip stream begins with the magic bytes 0x1f 0x8b.
    assert raw[:2] == b"\x1f\x8b"
    # Decompresses back to exactly the fixture's source rootfs (round-trip).
    assert gzip.decompress(raw) == FIXTURE_ROOTFS


def test_publish_compression_is_deterministic(tmp_path, build_output) -> None:
    # Two independent publishes of the same build must produce byte-identical
    # compressed archives (and therefore identical digests) — the release is the
    # unit of change for a reproducible distro.
    feed_a = tmp_path / "a"
    feed_b = tmp_path / "b"
    publish(build_output, "server", "1.0.0", feed_a)
    publish(build_output, "server", "1.0.0", feed_b)

    gz_a = _stored_bytes(feed_a, "server", "1.0.0", "rootfs.img.gz").read_bytes()
    gz_b = _stored_bytes(feed_b, "server", "1.0.0", "rootfs.img.gz").read_bytes()
    assert gz_a == gz_b

    # ...and the whole manifest.json is byte-identical too.
    ma = manifest_path(feed_a, "server", "1.0.0")
    mb = manifest_path(feed_b, "server", "1.0.0")
    assert ma.read_bytes() == mb.read_bytes()


def test_publish_digest_matches_stored_bytes(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    for name in ARTIFACTS:
        raw = _stored_bytes(feed, "server", "1.0.0", name).read_bytes()
        assert _digest_of(feed, "server", "1.0.0", name) == hashlib.sha256(raw).hexdigest()


# --- signing --------------------------------------------------------------

def test_publish_signs_manifest_and_artifacts(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    manifest = publish(build_output, "server", "1.0.0", feed, signer=signer)
    assert manifest.signature  # manifest is signed
    for artifact in manifest.artifacts:
        assert artifact.signature  # every artifact is signed


def test_publish_manifest_verifies(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    manifest = publish(build_output, "server", "1.0.0", feed, signer=signer)
    assert manifest.verify(signer) is True
    # Tampering with the payload must break verification.
    manifest.edition = "core"
    assert manifest.verify(signer) is False


def test_publish_manifest_signature_survives_roundtrip(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=signer)
    # Re-read the manifest from disk (no in-memory signature) and verify.
    on_disk = read_manifest(feed, "server", "1.0.0")
    assert on_disk.verify(signer) is True


def test_publish_manifest_signature_is_keyed(tmp_path, build_output, signer) -> None:
    # A keyed signer: a wrong key must not verify.
    key = HmacSigner(b"publishing-key")
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=key)
    on_disk = read_manifest(feed, "server", "1.0.0")
    assert on_disk.verify(key) is True
    assert on_disk.verify(HmacSigner(b"wrong-key")) is False


# --- index / channel wiring ----------------------------------------------

def test_publish_registers_release_and_channel(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    index = read_index(feed)
    assert any(r.edition == "server" and r.version == "1.0.0" for r in index.releases)
    assert index.channel("stable").version == "1.0.0"


def test_publish_custom_channel(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "core", "2.0.0", feed, channel="testing")
    index = read_index(feed)
    assert index.channel("testing").version == "2.0.0"
    assert index.channel("stable") is None  # nothing pointed at stable yet


def test_publish_channel_none_does_not_touch_index(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    # Pre-seed a signed index + channel pointer.
    seed = FeedIndex(releases=[], channels={"stable": "9.9.9"})
    seed.sign(signer)
    from pengyos_release.feed import write_index, write_channel_pointer

    write_index(feed, seed)
    write_channel_pointer(feed, "stable", "9.9.9")

    publish(build_output, "server", "1.0.0", feed, channel=None)
    # The pre-existing pointer is untouched, and the release dir still exists.
    assert read_index(feed).channel("stable").version == "9.9.9"
    assert version_dir(feed, "server", "1.0.0").is_dir()


def test_publish_is_idempotent(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    publish(build_output, "server", "1.0.0", feed)  # republish same edition+version
    index = read_index(feed)
    matches = [r for r in index.releases if r.edition == "server" and r.version == "1.0.0"]
    assert len(matches) == 1  # replaced, not duplicated


def test_publish_appends_new_version(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    publish(build_output, "server", "1.1.0", feed)
    index = read_index(feed)
    versions = {r.version for r in index.releases if r.edition == "server"}
    assert versions == {"1.0.0", "1.1.0"}


# --- validation -----------------------------------------------------------

def test_published_feed_validates(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=signer)
    res = validate_feed(feed, check_signatures=True, signer=signer)
    assert res.ok is True, res.errors
    assert res.manifests_validated == 1
    assert res.index_valid is True


def test_published_manifest_validates(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    manifest = publish(build_output, "server", "1.0.0", feed, signer=signer)
    assert manifest.validate() is manifest


# --- errors ---------------------------------------------------------------

def test_publish_missing_build_raises(tmp_path) -> None:
    with pytest.raises(BuildOutputError):
        publish(tmp_path / "nope", "server", "1.0.0", tmp_path / "feed")


def test_publish_missing_artifact_raises(tmp_path) -> None:
    # A build output without the rootfs image must raise a build-scoped error.
    build = tmp_path / "build"
    build.mkdir()
    with pytest.raises(BuildOutputError):
        publish(build, "server", "1.0.0", tmp_path / "feed")


def test_publish_bad_version_raises(tmp_path, build_output) -> None:
    with pytest.raises(BuildOutputError):
        publish(build_output, "server", "not-a-version", tmp_path / "feed")


def test_publish_empty_edition_raises(tmp_path, build_output) -> None:
    with pytest.raises(BuildOutputError):
        publish(build_output, "", "1.0.0", tmp_path / "feed")


def test_publish_non_dotted_version_raises(tmp_path, build_output) -> None:
    with pytest.raises(BuildOutputError):
        publish(build_output, "server", "1", tmp_path / "feed")


def test_parse_version_rejects_non_dotted_directly() -> None:
    # Sanity pin on the single version validator publish relies on.
    from pengyos_release.errors import ManifestError

    with pytest.raises(ManifestError):
        parse_version("1")


def test_published_index_is_valid_json(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    parsed = json.loads(index_path(feed).read_text(encoding="utf-8"))
    assert parsed["schema"] == "pengyos-release/index"
