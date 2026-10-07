"""Tests for pengyos_backup.restorer — the proven-restore core.

These exercise the tree hash, the fixture-repo reader, the mockable runners
(``SimulatedRunner`` / ``FakeRunner`` / ``SubprocessRunner``), and
:func:`verify_restore` — the happy path, a failed restore, and a tree-hash
mismatch. No restic installed, no network, no root.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from pengyos_backup.errors import RepoError
from pengyos_backup.restorer import (
    FIXTURE_REPO,
    FIXTURE_SNAPSHOT_ID,
    FIXTURE_TREE_HASH,
    FakeRunner,
    RunResult,
    SimulatedRunner,
    SubprocessRunner,
    Snapshot,
    TreeEntry,
    load_snapshot,
    restore_argv,
    scan_tree,
    tree_hash,
    verify_restore,
)


# --- tree_hash ---------------------------------------------------------------


def test_tree_hash_is_deterministic():
    entries = [
        TreeEntry("b", 2, "bb"),
        TreeEntry("a", 1, "aa"),
    ]
    assert tree_hash(entries) == tree_hash(entries)


def test_tree_hash_is_order_independent():
    entries = [
        TreeEntry("b", 2, "bb"),
        TreeEntry("a", 1, "aa"),
    ]
    # Sorting by path first means the digest does not depend on input order.
    assert tree_hash(entries) == tree_hash(list(reversed(entries)))


def test_tree_hash_changes_with_content():
    a = tree_hash([TreeEntry("a", 1, "aa")])
    b = tree_hash([TreeEntry("a", 2, "xx")])
    assert a != b


def test_tree_hash_matches_fixture_golden():
    # The fixture's golden tree_hash is computed by this very function over its
    # manifest entries, so the fixture must round-trip.
    snap = load_snapshot(FIXTURE_REPO)
    assert snap.tree_hash == FIXTURE_TREE_HASH
    assert tree_hash(list(snap.entries)) == FIXTURE_TREE_HASH


# --- scan_tree ---------------------------------------------------------------


def test_scan_tree_collects_files_relative_paths(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "hostname").write_bytes(b"host\n")
    (tmp_path / "home").mkdir(parents=True)
    (tmp_path / "home" / "user").write_bytes(b"data")

    entries = scan_tree(tmp_path)
    by_path = {e.path: e for e in entries}
    assert set(by_path) == {"etc/hostname", "home/user"}
    assert by_path["etc/hostname"].size == 5
    assert by_path["etc/hostname"].content_hash == _sha256(b"host\n")


def test_scan_tree_ignores_directories_and_symlinks(tmp_path):
    (tmp_path / "regular").write_bytes(b"x")
    (tmp_path / "adir").mkdir()
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "regular")

    entries = scan_tree(tmp_path)
    assert [e.path for e in entries] == ["regular"]


def test_scan_tree_empty_dir_is_empty(tmp_path):
    assert scan_tree(tmp_path) == []


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


# --- load_snapshot / repo ----------------------------------------------------


def test_load_snapshot_reads_fixture():
    snap = load_snapshot(FIXTURE_REPO)
    assert isinstance(snap, Snapshot)
    assert snap.id == FIXTURE_SNAPSHOT_ID
    assert snap.tree_hash == FIXTURE_TREE_HASH
    assert len(snap.entries) == 5
    assert snap.entries[0].path == "etc/pengyos-release"


def test_load_snapshot_missing_repo_raises(tmp_path):
    with pytest.raises(RepoError, match="no snapshot.json"):
        load_snapshot(tmp_path)


def test_load_snapshot_non_fixture_raises(tmp_path):
    (tmp_path / "snapshot.json").write_text(
        json.dumps({"format": "wrong", "id": "x", "tree_hash": "y", "entries": []}),
        encoding="utf-8",
    )
    with pytest.raises(RepoError, match="not a pengyos-backup fixture"):
        load_snapshot(tmp_path)


def test_load_snapshot_bad_json_raises(tmp_path):
    (tmp_path / "snapshot.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(RepoError, match="invalid snapshot.json"):
        load_snapshot(tmp_path)


# --- restore_argv ------------------------------------------------------------


def test_restore_argv_shape():
    argv = restore_argv("restic", "snap1", "/repo", "/target")
    assert argv == [
        "restic",
        "restore",
        "snap1",
        "--repo",
        "/repo",
        "--target",
        "/target",
    ]


def test_restore_argv_rustic():
    assert restore_argv("rustic", "s", "/r", "/t")[0] == "rustic"


# --- SimulatedRunner ---------------------------------------------------------


def test_simulated_runner_restores_fixture_tree(tmp_path):
    target = tmp_path / "restored"
    result = SimulatedRunner().restore(
        binary="restic",
        snapshot_id=FIXTURE_SNAPSHOT_ID,
        repo=str(FIXTURE_REPO),
        target=str(target),
    )
    assert result.ok
    restored = sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file())
    assert restored == [
        "etc/hostname",
        "etc/pengyos-release",
        "home/operator/.config/profile.sh",
        "home/operator/README.md",
        "var/log/pengyos.log",
    ]


def test_simulated_runner_reports_missing_blob(tmp_path):
    # A copy of the fixture repo with one blob deleted -> the restore must fail.
    # (Only the temp copy is touched; FIXTURE_REPO is absolute, so joining it
    # onto another path would discard the left operand and hit the real repo.)
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    next((repo / "content").iterdir()).unlink()

    result = SimulatedRunner().restore(
        binary="restic",
        snapshot_id=FIXTURE_SNAPSHOT_ID,
        repo=str(repo),
        target=str(tmp_path / "t"),
    )
    assert not result.ok
    assert "missing" in result.stderr.lower()


def test_simulated_runner_snapshot_mismatch(tmp_path):
    result = SimulatedRunner().restore(
        binary="restic",
        snapshot_id="does-not-exist",
        repo=str(FIXTURE_REPO),
        target=str(tmp_path / "t"),
    )
    assert not result.ok
    assert "not found" in result.stderr.lower()


# --- FakeRunner --------------------------------------------------------------


def test_fake_runner_records_restore_calls():
    fake = FakeRunner()
    result = fake.restore(
        binary="restic",
        snapshot_id="s",
        repo="/r",
        target="/t",
    )
    assert result.ok
    assert fake.calls == [
        ["restic", "restore", "s", "--repo", "/r", "--target", "/t"]
    ]


def test_fake_runner_scripted_failure():
    fake = FakeRunner()
    fake.respond(
        ["restic", "restore"],
        RunResult(argv=["restic", "restore"], returncode=1, stderr="boom"),
    )
    result = fake.restore(
        binary="restic",
        snapshot_id="s",
        repo="/r",
        target="/t",
    )
    assert not result.ok
    assert result.stderr == "boom"


def test_subprocess_runner_missing_binary_returns_nonzero():
    # restic is not installed in the test env; this must be a clean non-zero
    # result, not a crash.
    result = SubprocessRunner().restore(
        binary="restic-does-not-exist",
        snapshot_id="s",
        repo="/r",
        target="/t",
    )
    assert result.returncode == 127


# --- verify_restore ----------------------------------------------------------


def test_verify_restore_ok(tmp_path):
    target = tmp_path / "restored"
    result = verify_restore(
        FIXTURE_REPO,
        target,
        snapshot_id=FIXTURE_SNAPSHOT_ID,
    )
    assert result.ok
    assert result.tree_hash == FIXTURE_TREE_HASH
    assert result.restored == sorted(
        {
            "etc/hostname",
            "etc/pengyos-release",
            "home/operator/.config/profile.sh",
            "home/operator/README.md",
            "var/log/pengyos.log",
        }
    )


def test_verify_restore_restores_into_target(tmp_path):
    target = tmp_path / "restored"
    result = verify_restore(FIXTURE_REPO, target)
    assert result.ok
    assert (target / "etc" / "pengyos-release").read_bytes() == b"PengyOS 1.0 (Serpo)\n"


def test_verify_restore_snapshot_not_found(tmp_path):
    result = verify_restore(
        FIXTURE_REPO,
        tmp_path / "t",
        snapshot_id="nope",
    )
    assert not result.ok
    assert "not found" in result.error


# --- corrupt / missing fixture repo (the proof has teeth) --------------------


def _copy_fixture(tmp_path: Path, name: str) -> Path:
    """Copy the fixture repo into *tmp_path* under *name* (never touch the real one)."""

    import shutil

    repo = tmp_path / name
    shutil.copytree(FIXTURE_REPO, repo)
    return repo


def test_verify_restore_reports_corrupt_snapshot_json(tmp_path):
    # A snapshot.json that is not valid JSON: the proof must fail cleanly
    # (ok=False with an error), never raise.
    repo = _copy_fixture(tmp_path, "corrupt")
    (repo / "snapshot.json").write_text("{ not valid json", encoding="utf-8")

    result = verify_restore(repo, tmp_path / "t")
    assert not result.ok
    assert result.tree_hash == ""
    assert "invalid snapshot.json" in result.error


def test_verify_restore_reports_non_fixture_repo(tmp_path):
    # A snapshot.json with the wrong format marker: a clean proof failure.
    repo = _copy_fixture(tmp_path, "wrong-format")
    doc = json.loads((repo / "snapshot.json").read_text(encoding="utf-8"))
    doc["format"] = "not-a-pengyos-fixture"
    (repo / "snapshot.json").write_text(json.dumps(doc), encoding="utf-8")

    result = verify_restore(repo, tmp_path / "t")
    assert not result.ok
    assert "not a pengyos-backup fixture" in result.error


def test_verify_restore_reports_missing_snapshot_json(tmp_path):
    # A repo with no snapshot.json at all: a clean proof failure.
    repo = _copy_fixture(tmp_path, "no-snapshot")
    (repo / "snapshot.json").unlink()

    result = verify_restore(repo, tmp_path / "t")
    assert not result.ok
    assert "no snapshot.json" in result.error


def test_verify_restore_reports_missing_repo_dir(tmp_path):
    # A repo path that does not exist: a clean proof failure (ok=False), not a
    # raised exception.
    result = verify_restore(tmp_path / "nope", tmp_path / "t")
    assert not result.ok
    assert "not found" in result.error.lower() or "no snapshot" in result.error.lower()


def test_verify_restore_detects_hash_mismatch(tmp_path):
    # A runner that restores a *different* tree: verify_restore must catch the
    # digest mismatch.
    class _TamperingRunner(SimulatedRunner):
        def restore(self, *, binary, snapshot_id, repo, target):
            result = super().restore(
                binary=binary,
                snapshot_id=snapshot_id,
                repo=repo,
                target=target,
            )
            # Corrupt one restored file so the tree no longer hashes to golden.
            if result.ok:
                (Path(target) / "etc" / "hostname").write_bytes(b"tampered\n")
            return result

    result = verify_restore(
        FIXTURE_REPO,
        tmp_path / "t",
        runner=_TamperingRunner(),
    )
    assert not result.ok
    assert "hash" in (result.error or "").lower()


def test_verify_restore_restore_failure_is_reported(tmp_path):
    # A runner whose restore fails: verify_restore must surface the error.
    fake = FakeRunner()
    fake.respond(
        ["restic", "restore"],
        RunResult(argv=["restic", "restore"], returncode=1, stderr="restore blew up"),
    )
    result = verify_restore(
        FIXTURE_REPO,
        tmp_path / "t",
        snapshot_id=FIXTURE_SNAPSHOT_ID,
        runner=fake,
    )
    assert not result.ok
    assert "restore blew up" in result.error


def test_verify_restore_defaults_to_offline_simulation(tmp_path):
    # With no runner passed, verify_restore must use the offline simulation:
    # it reproduces the fixture tree and hashes to golden with no restic.
    result = verify_restore(FIXTURE_REPO, tmp_path / "t")
    assert result.ok
    assert result.tree_hash == FIXTURE_TREE_HASH
