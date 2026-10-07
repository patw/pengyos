"""Tests for the ``retain`` feed operation and its CLI handler.

``retain`` prunes old versions from a feed, keeping the newest ``keep`` per
edition and never pruning a version a channel points at. By default it is a
dry run; applied, it deletes the pruned version dirs and re-signs the index so
the feed stays consistent. These exercise the real publish -> retain path end to
end against the committed fake build-output fixture (``build_output`` in
conftest) plus targeted error/edge cases.
"""

from __future__ import annotations

import json

import pytest

from pengyos_release import retain, verify_feed
from pengyos_release.errors import FeedError
from pengyos_release.feed import (
    index_path,
    list_all,
    read_channel_pointer,
    read_index,
    validate_feed,
    version_dir,
)
from pengyos_release.manifest import parse_version
from pengyos_release.publish import publish
from pengyos_release.signing import HmacSigner


def _versions(feed, edition: str) -> list[str]:
    """The (edition, version) pairs still present for ``edition``, newest-first."""
    return [v for e, v in list_all(feed) if e == edition]


def _publish_n(feed, build_output, edition, versions, channel="stable", signer=None):
    for v in versions:
        publish(build_output, edition, v, feed, channel=channel, signer=signer)


# --- dry run (default) ----------------------------------------------------

def test_retain_dry_run_by_default(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])

    res = retain(feed, keep=2)  # dry run by default
    assert res.dry_run is True
    # The two newest are kept; the two oldest would be pruned.
    assert res.kept == [("server", "1.2.0"), ("server", "1.3.0")]
    assert res.pruned == [("server", "1.0.0"), ("server", "1.1.0")]
    # Nothing was actually removed from disk.
    assert _versions(feed, "server") == ["1.0.0", "1.1.0", "1.2.0", "1.3.0"]
    assert res.removed == []


def test_retain_dry_run_leaves_feed_intact(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0"])
    before = index_path(feed).read_bytes()

    retain(feed, keep=1, dry_run=True)
    assert index_path(feed).read_bytes() == before  # index untouched
    assert len(_versions(feed, "server")) == 3


# --- keep exactly N -------------------------------------------------------

def test_retain_keeps_exactly_newest_n(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0"])

    res = retain(feed, keep=3, dry_run=False)
    assert res.pruned == [("server", "1.0.0"), ("server", "1.1.0")]
    assert _versions(feed, "server") == ["1.2.0", "1.3.0", "1.4.0"]  # exactly 3 kept


def test_retain_keep_one_keeps_only_newest(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "core", ["0.1.0", "0.2.0", "0.3.0"])

    retain(feed, keep=1, dry_run=False)
    assert _versions(feed, "core") == ["0.3.0"]


def test_retain_keep_all_keeps_everything(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "core", ["0.1.0", "0.2.0"])

    res = retain(feed, keep=10, dry_run=False)
    assert res.pruned == []
    assert _versions(feed, "core") == ["0.1.0", "0.2.0"]


def test_retain_orders_by_version_not_name(tmp_path, build_output) -> None:
    # "10.0.0" > "9.0.0" numerically; retain must keep the numerically-newest,
    # not the lexicographically-last. channel=None so nothing is protected.
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.9.0", "1.10.0", "1.2.0"], channel=None)

    retain(feed, keep=1, dry_run=False)
    assert _versions(feed, "server") == ["1.10.0"]


# --- never prune the pointed-at version -----------------------------------

def test_retain_never_prunes_pointed_at_version(tmp_path, build_output) -> None:
    # stable points at 1.0.0 (the oldest). It must be protected even though it is
    # outside the newest `keep`. retain reads the protected set from the *signed
    # index*, so mirror the re-pointed pointer into the index first.
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0", "1.3.0"])
    from pengyos_release.feed import regenerate_index, write_channel_pointer

    # Re-point stable at the oldest version (a back-published / unpromoted tier).
    write_channel_pointer(feed, "stable", "1.0.0")
    regenerate_index(feed)  # mirror the pointer into the signed index

    res = retain(feed, keep=1, dry_run=False)
    # 1.0.0 is protected (pointed at); 1.1.0 and 1.2.0 are pruned; 1.3.0 kept.
    assert ("server", "1.0.0") not in res.pruned
    assert _versions(feed, "server") == ["1.0.0", "1.3.0"]
    assert sorted(v for _, v in res.pruned) == ["1.1.0", "1.2.0"]


def test_retain_pointed_at_survives_when_outside_keep(tmp_path, build_output) -> None:
    # Even a pointed-at version far outside the newest keep survives.
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "2.0.0", "2.1.0", "2.2.0"])
    from pengyos_release.feed import regenerate_index, write_channel_pointer

    write_channel_pointer(feed, "stable", "1.0.0")
    regenerate_index(feed)  # mirror the pointer into the signed index

    retain(feed, keep=1, dry_run=False)
    assert "1.0.0" in _versions(feed, "server")
    assert _versions(feed, "server")[-1] == "2.2.0"  # newest still kept


# --- multiple editions ----------------------------------------------------

def test_retain_per_edition(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0"])
    _publish_n(feed, build_output, "core", ["0.1.0", "0.2.0", "0.3.0", "0.4.0"])

    res = retain(feed, keep=2, dry_run=False)
    assert _versions(feed, "server") == ["1.1.0", "1.2.0"]
    assert _versions(feed, "core") == ["0.3.0", "0.4.0"]
    assert res.pruned == [("core", "0.1.0"), ("core", "0.2.0"), ("server", "1.0.0")]


# --- applied mode leaves a consistent feed --------------------------------

def test_retain_applied_rebuilds_index(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0"], signer=signer)

    retain(feed, keep=1, dry_run=False, signer=signer)

    # The pruned release is gone from the index too.
    index = read_index(feed)
    versions = {r.version for r in index.releases if r.edition == "server"}
    assert versions == {"1.2.0"}
    # The surviving channel pointer still resolves.
    assert index.channel("stable").version == "1.2.0"


def test_retain_applied_feed_still_verifies(tmp_path, build_output, signer) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0"], signer=signer)

    retain(feed, keep=1, dry_run=False, signer=signer)

    res = verify_feed(feed, signer=signer)
    assert res.ok, res.problems
    assert res.manifests_verified == 1
    assert res.artifacts_verified == 5


def test_retain_applied_preserves_channel_pointers(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    _publish_n(feed, build_output, "server", ["1.0.0", "1.1.0", "1.2.0"], channel="stable")
    _publish_n(feed, build_output, "core", ["0.1.0", "0.2.0", "0.3.0"], channel="testing")

    retain(feed, keep=1, dry_run=False)
    assert read_channel_pointer(feed, "stable") == "1.2.0"
    assert read_channel_pointer(feed, "testing") == "0.3.0"


# --- edge cases -----------------------------------------------------------

def test_retain_empty_feed(tmp_path) -> None:
    feed = tmp_path / "feed"
    feed.mkdir()
    res = retain(feed, keep=3)
    assert res.kept == []
    assert res.pruned == []
    assert res.dry_run is True


def test_retain_single_version_keeps_it(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    res = retain(feed, keep=1)
    assert res.pruned == []
    assert res.kept == [("server", "1.0.0")]


# --- errors ---------------------------------------------------------------

def test_retain_missing_feed_raises(tmp_path) -> None:
    with pytest.raises(FeedError):
        retain(tmp_path / "nope", keep=3)


def test_retain_keep_below_one_raises(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    feed.mkdir()
    with pytest.raises(FeedError):
        retain(feed, keep=0)


def test_retain_bad_signer_raises(tmp_path, build_output) -> None:
    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    with pytest.raises(FeedError):
        retain(feed, keep=1, signer="not-a-signer")  # type: ignore[arg-type]


def test_retain_default_signer_roundtrip(tmp_path, build_output) -> None:
    # Stub-published feed retained with the default stub still verifies (no key).
    from pengyos_release.feed import DEFAULT_SIGNER

    feed = tmp_path / "feed"
    publish(build_output, "server", "1.0.0", feed)
    publish(build_output, "server", "1.1.0", feed)
    retain(feed, keep=1, dry_run=False)
    res = verify_feed(feed, signer=DEFAULT_SIGNER)
    assert res.ok, res.problems


# --- CLI ------------------------------------------------------------------

def test_retain_cli_dry_run_json(tmp_path, build_output, capsys) -> None:
    from pengyos_release.cli import main

    feed = tmp_path / "feed"
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", v, "--feed", str(feed)]) == 0

    code = main(["retain", "--feed", str(feed), "--keep", "1", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    # JSON serialises the (edition, version) tuples as lists.
    assert out["pruned"] == [["server", "1.0.0"], ["server", "1.1.0"]]
    assert out["kept"] == [["server", "1.2.0"]]
    # Dry run did not delete anything.
    assert len(_versions(feed, "server")) == 3


def test_retain_cli_applied_deletes_and_reports(tmp_path, build_output, capsys) -> None:
    from pengyos_release.cli import main

    feed = tmp_path / "feed"
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", v, "--feed", str(feed)]) == 0

    # --apply actually prunes (retain is a dry run by default).
    code = main(["retain", "--feed", str(feed), "--keep", "1", "--apply"])
    assert code == 0
    assert _versions(feed, "server") == ["1.2.0"]
    # The feed is still valid after the prune.
    assert validate_feed(feed).ok


def test_retain_cli_applied_reports_removed(tmp_path, build_output, capsys) -> None:
    from pengyos_release.cli import main

    feed = tmp_path / "feed"
    for v in ("1.0.0", "1.1.0", "1.2.0"):
        assert main(["publish", "--build", str(build_output), "--edition", "server", "--version", v, "--feed", str(feed)]) == 0

    code = main(["retain", "--feed", str(feed), "--keep", "1", "--apply", "--json"])
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is False
    # Two versions were actually removed from disk.
    assert len(out["removed"]) == 2
    assert not (feed / "pengyos" / "server" / "1.0.0").exists()


def test_retain_cli_bad_keep_exit2(tmp_path, capsys) -> None:
    from pengyos_release.cli import main

    feed = tmp_path / "feed"
    feed.mkdir()
    code = main(["retain", "--feed", str(feed), "--keep", "0"])
    assert code == 2
    assert "--keep must be >= 1" in capsys.readouterr().err


def test_retain_cli_missing_feed_exit2(tmp_path, capsys) -> None:
    from pengyos_release.cli import main

    code = main(["retain", "--feed", str(tmp_path / "nope"), "--keep", "3"])
    assert code == 2
    assert "feed directory not found" in capsys.readouterr().err


def test_retain_cli_keeps_parser_help(capsys) -> None:
    # Retain is a first-class subcommand with --keep + --dry-run.
    with pytest.raises(SystemExit) as exc:
        from pengyos_release.cli import main

        main(["retain", "--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "--keep" in captured.out
    assert "--dry-run" in captured.out
    assert "--apply" in captured.out
