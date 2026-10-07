"""Tests for the ``index`` (``regenerate_index``) and ``verify`` (``verify_feed``)
feed operations.

``regenerate_index`` rebuilds the signed index + channel pointers from the
on-disk manifests and the durable ``channels/<name>`` mirror — the recovery path
when ``index.json`` is missing or corrupt. ``verify_feed`` re-reads every stored
artifact and re-checks its sha256 + every signature — the ``verify`` command's
job. Both are exercised end to end against the committed fake build-output
fixture (``build_output`` in conftest) plus targeted corruption tests.
"""

from __future__ import annotations

import json

import pytest

from pengyos_release import regenerate_index, verify_feed
from pengyos_release.errors import FeedError
from pengyos_release.feed import (
    index_path,
    read_channel_pointer,
    read_index,
    validate_feed,
    write_channel_pointer,
)
from pengyos_release.manifest import parse_version
from pengyos_release.publish import publish
from pengyos_release.signing import HmacSigner


# --- regenerate_index -------------------------------------------------------

def test_regenerate_index_rebuilds_from_manifests(tmp_path, build_output, signer) -> None:
    # Publish, then destroy index.json (channel files survive): the manifests on
    # disk are the source of truth. regenerate_index must rebuild the signed
    # index + channel pointers from them.
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=signer)
    index_path(feed).unlink()

    index = regenerate_index(feed, signer=signer)
    assert any(r.edition == "server" and r.version == "1.0.0" for r in index.releases)
    # The rebuilt index is signed and verifies structurally.
    assert index.signature
    assert read_channel_pointer(feed, "stable") == "1.0.0"
    res = validate_feed(feed, check_signatures=True, signer=signer)
    assert res.ok, res.errors


def test_regenerate_index_reads_channel_pointers(tmp_path, build_output) -> None:
    # Two versions, with the durable channel files pointing at specific ones.
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    publish(build_output, "server", "1.1.0", feed, channel="testing")
    # Point stable back at the older version (simulating an unpublish/promotion).
    write_channel_pointer(feed, "stable", "1.0.0")

    index = regenerate_index(feed)
    assert index.channel("stable").version == "1.0.0"
    assert index.channel("testing").version == "1.1.0"
    # The channel files are rewritten to match the rebuilt index.
    assert read_channel_pointer(feed, "stable") == "1.0.0"
    assert read_channel_pointer(feed, "testing") == "1.1.0"


def test_regenerate_index_preserves_multiple_releases(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=signer)
    publish(build_output, "server", "1.1.0", feed, signer=signer)
    publish(build_output, "core", "0.1.0", feed, channel="testing", signer=signer)
    # Corrupt the index so it no longer reflects the on-disk releases.
    index_path(feed).write_text("{ this is not valid json", encoding="utf-8")

    index = regenerate_index(feed, signer=signer)
    versions = {r.version for r in index.releases if r.edition == "server"}
    assert versions == {"1.0.0", "1.1.0"}
    # core/0.1.0 is recovered too, and the index re-verifies.
    assert any(r.edition == "core" for r in index.releases)
    assert validate_feed(feed, check_signatures=True, signer=signer).ok


def test_regenerate_index_is_deterministic(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    publish(build_output, "server", "1.1.0", feed)
    first = index_path(feed).read_bytes()
    regenerate_index(feed)
    second = index_path(feed).read_bytes()
    assert first == second  # byte-identical, canonical rebuild


def test_regenerate_index_channel_filter(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    publish(build_output, "server", "1.1.0", feed, channel="testing")
    # Only (re)write the stable pointer; testing is left untouched.
    index = regenerate_index(feed, channels=["stable"])
    assert "stable" in index.channels
    assert "testing" not in index.channels


def test_regenerate_index_requires_signer(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    with pytest.raises(FeedError):
        regenerate_index(feed, signer="not-a-signer")  # type: ignore[arg-type]


def test_regenerate_index_missing_dir_raises(tmp_path) -> None:
    with pytest.raises(FeedError):
        regenerate_index(tmp_path / "nope")


def test_regenerate_index_default_signer_roundtrip(tmp_path, build_output) -> None:
    # Published with the default stub; regenerate with the default stub too. The
    # rebuilt index must verify against the default stub (deterministic, keyed to
    # itself).
    from pengyos_release.feed import DEFAULT_SIGNER

    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    index = regenerate_index(feed)
    assert index.verify(DEFAULT_SIGNER) is True


def test_regenerate_index_empty_feed(tmp_path) -> None:
    feed = tmp_path / "feed"
    feed.mkdir()
    index = regenerate_index(feed)
    assert index.releases == []
    assert index.channels == {}
    assert index_path(feed).is_file()  # an (empty) signed index is written


# --- verify_feed ------------------------------------------------------------

def test_verify_feed_ok(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=signer)
    res = verify_feed(feed, signer=signer)
    assert res.ok, res.problems
    assert res.manifests_verified == 1
    assert res.artifacts_verified == 5  # one per published artifact
    assert res.index_valid is True


def test_verify_feed_ok_with_default_stub(tmp_path, build_output) -> None:
    # Published with the default stub; verify with the default stub (no key).
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    res = verify_feed(feed)
    assert res.ok, res.problems


def test_verify_feed_catches_hash_mismatch(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=signer)
    # Tamper the stored rootfs: append a byte so its digest no longer matches.
    gz = feed / "pengyos" / "server" / "1.0.0" / "rootfs.img.gz"
    gz.write_bytes(gz.read_bytes() + b"TAMPERED")

    res = verify_feed(feed, signer=signer)
    assert res.ok is False
    assert any("rootfs.img.gz" in e and "digest mismatch" in e for e in res.problems)


def test_verify_feed_catches_missing_index(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    index_path(feed).unlink()
    res = verify_feed(feed)
    assert res.ok is False
    assert any("missing signed index" in e for e in res.problems)


def test_verify_feed_catches_bad_signature(tmp_path, build_output) -> None:
    # Keyed feed verified with the wrong key must fail.
    key = HmacSigner(b"publishing-key")
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=key)
    res = verify_feed(feed, signer=HmacSigner(b"wrong-key"))
    assert res.ok is False
    assert any("signature did not verify" in e for e in res.problems)


def test_verify_feed_catches_dangling_pointer(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    # Point stable at a version that has no manifest (a lost/pruned release).
    write_channel_pointer(feed, "stable", "9.9.9")
    regenerate_index(feed)  # mirror the pointer into the signed index

    res = verify_feed(feed)
    assert res.ok is False
    assert any("dangles" in e for e in res.problems)


def test_verify_feed_hash_only_without_signer(tmp_path, build_output, signer) -> None:
    # check_signatures=False re-checks hashes but skips signatures. A keyed feed
    # verifies (hashes are right) even with no signer, and a tampered file still
    # fails — proving the hash path runs without a signer.
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=HmacSigner(b"key"))

    res = verify_feed(feed, check_signatures=False, signer=None)
    assert res.ok, res.problems  # hashes all match; signatures not checked

    # Tamper, then hash-only verify must catch it.
    gz = feed / "pengyos" / "server" / "1.0.0" / "rootfs.img.gz"
    gz.write_bytes(gz.read_bytes() + b"x")
    res = verify_feed(feed, check_signatures=False, signer=None)
    assert res.ok is False
    assert any("digest mismatch" in e for e in res.problems)


def test_verify_feed_requires_signer(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    with pytest.raises(FeedError):
        verify_feed(feed, check_signatures=True, signer="not-a-signer")  # type: ignore[arg-type]


def test_verify_feed_missing_dir_raises(tmp_path) -> None:
    with pytest.raises(FeedError):
        verify_feed(tmp_path / "nope")


def test_verify_feed_counts_artifacts(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed, signer=signer)
    publish(build_output, "core", "0.1.0", feed, channel="testing", signer=signer)
    res = verify_feed(feed, signer=signer)
    assert res.ok, res.problems
    assert res.manifests_verified == 2
    assert res.artifacts_verified == 10  # 2 releases x 5 artifacts


def test_verify_feed_valid_index_json(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    res = verify_feed(feed)
    assert res.ok, res.problems
    parsed = json.loads(index_path(feed).read_text(encoding="utf-8"))
    assert parsed["schema"] == "pengyos-release/index"
    assert parsed["channels"]["stable"] == "1.0.0"


# --- CLI: index + verify ----------------------------------------------------

def test_index_cli_reports_releases_and_channels(tmp_path, build_output, capsys) -> None:
    import json

    feed = tmp_path / "feed"
    from pengyos_release.cli import main

    assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", "1.0.0", "--feed", str(feed)]) == 0
    assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", "1.1.0", "--feed", str(feed)]) == 0

    # Corrupt the index, then rebuild it via the CLI.
    index_path(feed).write_text("{ broken", encoding="utf-8")
    code = main(["index", "--feed", str(feed), "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert {r["version"] for r in out["releases"] if r["edition"] == "server"} == {"1.0.0", "1.1.0"}
    assert out["channels"]["stable"] == "1.1.0"
    # The feed is valid again after the rebuild (default stub signs + verifies).
    from pengyos_release.feed import DEFAULT_SIGNER

    assert validate_feed(feed, check_signatures=True, signer=DEFAULT_SIGNER).ok


def test_verify_cli_ok(tmp_path, build_output, capsys) -> None:
    import json

    feed = tmp_path / "feed"
    from pengyos_release.cli import main

    assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", "1.0.0", "--feed", str(feed)]) == 0
    code = main(["verify", "--feed", str(feed), "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["artifacts_verified"] == 5


def test_verify_cli_catches_tamper(tmp_path, build_output, capsys) -> None:
    feed = tmp_path / "feed"
    from pengyos_release.cli import main

    assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", "1.0.0", "--feed", str(feed)]) == 0
    gz = feed / "pengyos" / "server" / "1.0.0" / "rootfs.img.gz"
    gz.write_bytes(gz.read_bytes() + b"TAMPER")

    code = main(["verify", "--feed", str(feed), "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert any("digest mismatch" in p for p in out["problems"])


def test_verify_cli_wrong_key_fails(tmp_path, build_output, capsys) -> None:
    feed = tmp_path / "feed"
    keyfile = tmp_path / "key"
    keyfile.write_bytes(b"publishing-key")
    from pengyos_release.cli import main

    assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", "1.0.0", "--feed", str(feed), "--key", str(keyfile)]) == 0
    # Wrong key -> signatures don't verify.
    code = main(["verify", "--feed", str(feed), "--key", "wrong-key", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False


def test_verify_cli_missing_key_ok_hash_only(tmp_path, build_output, capsys) -> None:
    # A stub-published feed verifies with the default stub (no --key).
    feed = tmp_path / "feed"
    from pengyos_release.cli import main

    assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", "1.0.0", "--feed", str(feed)]) == 0
    code = main(["verify", "--feed", str(feed), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0
    assert out["ok"] is True
