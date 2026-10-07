"""Grounding tests: the committed fixture build-output, the golden publish, and
the ``verify`` mutations.

Three things, all exercised against the *committed* fixture
(``tests/fixtures/sample-build/``) rather than in-memory bytes:

1. **The fixture is well-formed** — every expected artifact is present, non-empty,
   and the provenance files are valid JSON. This is the "ground" the rest of the
   suite builds on.
2. **Publish is golden** — publishing the fixed fixture is deterministic: two
   independent publishes are byte-identical, the published rootfs round-trips
   back to the fixture, the verbatim artifacts match the source bytes, and the
   recorded digests match the source digests.
3. **``verify`` catches each mutation** — an unsigned release, a tampered stored
   file (digest mismatch), and a dangling channel pointer are all caught, each
   reported distinctly.
"""

from __future__ import annotations

import gzip
import hashlib
import json

from pengyos_release import publish, verify_feed
from pengyos_release.feed import (
    index_path,
    manifest_path,
    read_channel_pointer,
    read_index,
    write_channel_pointer,
)
from pengyos_release.manifest import sha256_bytes
from pengyos_release.signing import StubSigner

FIXTURE = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "sample-build"

# The five artifacts the release tool collects from a build output.
ARTIFACT_SOURCES = {
    "rootfs.img.gz": "images/rootfs.img",
    "uki.efi": "images/uki.efi",
    "sbom.json": "sbom.json",
    "attestation.json": "attestation.json",
    "repro-report.txt": "repro-report.txt",
}
# The verbatim (uncompressed) artifacts: stored byte-for-byte from the build.
VERBATIM = ("uki.efi", "sbom.json", "attestation.json", "repro-report.txt")


# --- the fixture is well-formed (the ground) -------------------------------

def test_fixture_has_all_artifacts() -> None:
    assert FIXTURE.is_dir()
    for published in ARTIFACT_SOURCES:
        assert (FIXTURE / ARTIFACT_SOURCES[published]).is_file(), f"missing fixture artifact {published}"


def test_fixture_artifacts_nonempty() -> None:
    for published, relative in ARTIFACT_SOURCES.items():
        data = (FIXTURE / relative).read_bytes()
        assert len(data) > 0, f"fixture artifact {published} is empty"


def test_fixture_provenance_is_valid_json() -> None:
    for name in ("sbom.json", "attestation.json"):
        text = (FIXTURE / name).read_text(encoding="utf-8")
        # Must parse as JSON — a real SBOM/attestation is a JSON document.
        assert isinstance(json.loads(text), dict)


def test_fixture_is_committed_under_tests() -> None:
    # The fixture lives under tests/fixtures, never under /tmp or /dev/shm.
    assert "fixtures" in FIXTURE.parts


# --- publish is golden ----------------------------------------------------

def _published_manifest(feed, edition="server", version="1.0.0"):
    from pengyos_release.feed import read_manifest

    return read_manifest(feed, edition, version)


def test_publish_is_deterministic(tmp_path, build_output) -> None:
    # Two independent publishes of the *same fixed fixture* must be byte-identical:
    # the release is the unit of change for a reproducible distro, so identical
    # inputs yield identical output.
    feed_a = tmp_path / "a"
    feed_b = tmp_path / "b"
    publish(build_output, "server", "1.0.0", feed_a)
    publish(build_output, "server", "1.0.0", feed_b)

    for name in ARTIFACT_SOURCES:
        assert (feed_a / "pengyos" / "server" / "1.0.0" / name).read_bytes() == (
            feed_b / "pengyos" / "server" / "1.0.0" / name
        ).read_bytes()
    # ...and the signed manifest is byte-identical too.
    assert manifest_path(feed_a, "server", "1.0.0").read_bytes() == manifest_path(feed_b, "server", "1.0.0").read_bytes()


def test_publish_rootfs_roundtrips(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    raw = (feed / "pengyos" / "server" / "1.0.0" / "rootfs.img.gz").read_bytes()
    # A gzip stream begins with the magic bytes 0x1f 0x8b.
    assert raw[:2] == b"\x1f\x8b"
    # Decompresses back to exactly the fixture's source rootfs.
    assert gzip.decompress(raw) == (FIXTURE / "images" / "rootfs.img").read_bytes()


def test_publish_verbatim_artifacts_match_source(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    vdir = feed / "pengyos" / "server" / "1.0.0"
    for published, relative in ARTIFACT_SOURCES.items():
        if published in VERBATIM:
            assert (vdir / published).read_bytes() == (FIXTURE / relative).read_bytes()


def test_publish_digests_match_source_digests(tmp_path, build_output) -> None:
    # The recorded digest of every *verbatim* artifact is the sha256 of the
    # fixture source bytes (the digest is over the stored file).
    feed = tmp_path / "feed"
    manifest = publish(build_output, "server", "1.0.0", feed)
    for artifact in manifest.artifacts:
        if artifact.name in VERBATIM:
            relative = ARTIFACT_SOURCES[artifact.name]
            assert artifact.digest == sha256_bytes((FIXTURE / relative).read_bytes())


def test_publish_manifest_verifies(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    manifest = _published_manifest(feed)
    assert manifest.signature  # signed
    assert manifest.verify(StubSigner())  # verifies against the default stub


# --- verify catches each mutation -----------------------------------------

def test_verify_ok_on_golden_feed(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    res = verify_feed(feed)
    assert res.ok, res.problems
    assert res.manifests_verified == 1
    assert res.artifacts_verified == 5
    assert res.index_valid is True


def test_verify_catches_unsigned_manifest(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    # Clear the manifest signature: simulate a release that was never signed.
    path = manifest_path(feed, "server", "1.0.0")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["signature"] = ""
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    res = verify_feed(feed)
    assert res.ok is False
    # Distinct from a *wrong* signature ("did not verify"): this is unsigned.
    assert any("manifest has no signature" in e for e in res.problems)
    assert not any("did not verify" in e for e in res.problems)


def test_verify_catches_unsigned_index(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    # Clear the index signature: the table of contents was never signed.
    path = index_path(feed)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["signature"] = ""
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    res = verify_feed(feed)
    assert res.ok is False
    assert any("index has no signature" in e for e in res.problems)


def test_verify_catches_hash_mismatch(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    # Tamper a stored artifact: append a byte so its digest no longer matches.
    tampered = feed / "pengyos" / "server" / "1.0.0" / "sbom.json"
    tampered.write_bytes(tampered.read_bytes() + b"TAMPERED")

    res = verify_feed(feed)
    assert res.ok is False
    assert any("sbom.json" in e and "digest mismatch" in e for e in res.problems)


def test_verify_catches_missing_channel(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    # Point stable at a version with no manifest (a lost/pruned release), then
    # mirror the pointer into the signed index (as `index`/regenerate_index would).
    write_channel_pointer(feed, "stable", "9.9.9")
    from pengyos_release.feed import regenerate_index

    regenerate_index(feed)

    res = verify_feed(feed)
    assert res.ok is False
    assert any("dangles" in e for e in res.problems)
    assert any("9.9.9" in e for e in res.problems)


def test_verify_distinct_from_wrong_key(tmp_path, build_output) -> None:
    # A *wrong* (but present) signature is caught too, but reported as "did not
    # verify", not "has no signature" — the two mutations stay distinct.
    from pengyos_release.signing import HmacSigner

    key = HmacSigner(b"publishing-key")
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=key)
    res = verify_feed(feed, signer=HmacSigner(b"wrong-key"))
    assert res.ok is False
    assert any("signature did not verify" in e for e in res.problems)
    assert not any("has no signature" in e for e in res.problems)


def test_verify_still_reports_ok_after_valid_roundtrip(tmp_path, build_output) -> None:
    # A golden feed that has been re-indexed (regenerate_index) still verifies:
    # the golden property survives the index recovery path.
    from pengyos_release.feed import regenerate_index

    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    regenerate_index(feed)
    res = verify_feed(feed)
    assert res.ok, res.problems
    # The channel pointer still resolves to the golden version.
    assert read_channel_pointer(feed, "stable") == "1.0.0"
    assert read_index(feed).channel("stable").version == "1.0.0"
