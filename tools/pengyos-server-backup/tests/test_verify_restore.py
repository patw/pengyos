"""End-to-end tests for the ``verify-restore`` subcommand (spec.md Interface).

These run the CLI via ``python -m pengyos_backup`` (through the ``run_cli_fn``
fixture) against the committed fixture repo and assert on the restored tree and
the exit code. No restic installed, no network, no root.
"""

from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path

import pytest

from pengyos_backup.restorer import (
    FIXTURE_REPO,
    FIXTURE_SNAPSHOT_ID,
    FIXTURE_TREE_HASH,
)

# The fixture tree each file restores to (content-addressed blobs -> paths).
EXPECTED_FILES = sorted(
    {
        "etc/hostname",
        "etc/pengyos-release",
        "home/operator/.config/profile.sh",
        "home/operator/README.md",
        "var/log/pengyos.log",
    }
)


def _repo_arg() -> str:
    # run_cli_fn runs with cwd = repo root, so the fixture repo is relative.
    return str(FIXTURE_REPO)


def test_verify_restore_succeeds(run_cli_fn, tmp_path):
    out = tmp_path / "restored"
    proc = run_cli_fn(
        "verify-restore", "--repo", _repo_arg(), "--target", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    # The tree is reproduced on disk.
    assert (out / "etc" / "pengyos-release").read_bytes() == b"PengyOS 1.0 (Serpo)\n"
    # The golden tree hash is asserted in the output.
    assert FIXTURE_TREE_HASH in proc.stdout
    assert "verified" in proc.stdout.lower()


def test_verify_restore_restores_expected_files(run_cli_fn, tmp_path):
    out = tmp_path / "restored"
    proc = run_cli_fn(
        "verify-restore", "--repo", _repo_arg(), "--target", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    restored = {
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.is_file()
    }
    assert restored == set(EXPECTED_FILES)


def test_verify_restore_creates_target_dir(run_cli_fn, tmp_path):
    out = tmp_path / "nested" / "restored"  # does not exist yet
    proc = run_cli_fn(
        "verify-restore", "--repo", _repo_arg(), "--target", str(out)
    )
    assert proc.returncode == 0, proc.stderr
    assert out.is_dir()


def test_verify_restore_dry_run_prints_argv_without_restoring(run_cli_fn, tmp_path):
    out = tmp_path / "restored"
    proc = run_cli_fn(
        "verify-restore",
        "--repo",
        _repo_arg(),
        "--target",
        str(out),
        "--dry-run",
    )
    assert proc.returncode == 0, proc.stderr
    # The golden restore argv is printed...
    assert (
        f"restic restore {FIXTURE_SNAPSHOT_ID} --repo {_repo_arg()} "
        f"--target {out}"
    ) in proc.stdout
    # ...and nothing was restored.
    assert not out.exists()


def test_verify_restore_rustic_binary(run_cli_fn, tmp_path):
    # The binary is echoed in the dry-run argv; a real run restores the tree.
    out = tmp_path / "restored"
    proc = run_cli_fn(
        "verify-restore",
        "--repo",
        _repo_arg(),
        "--target",
        str(out),
        "--binary",
        "rustic",
    )
    assert proc.returncode == 0, proc.stderr
    assert (out / "etc" / "pengyos-release").read_bytes() == b"PengyOS 1.0 (Serpo)\n"

    proc = run_cli_fn(
        "verify-restore",
        "--repo",
        _repo_arg(),
        "--target",
        str(tmp_path / "dry"),
        "--binary",
        "rustic",
        "--dry-run",
    )
    assert proc.returncode == 0, proc.stderr
    assert "rustic restore" in proc.stdout


def test_verify_restore_missing_repo_returns_one(run_cli_fn, tmp_path):
    proc = run_cli_fn(
        "verify-restore",
        "--repo",
        str(tmp_path / "nope"),
        "--target",
        str(tmp_path / "t"),
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr.lower()


def test_verify_restore_corrupt_snapshot_returns_one(run_cli_fn, tmp_path):
    # A copy of the fixture repo with a blob deleted -> the restore fails cleanly.
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    content = repo / "content"
    (content / next(content.iterdir())).unlink()

    out = tmp_path / "restored"
    proc = run_cli_fn(
        "verify-restore", "--repo", str(repo), "--target", str(out)
    )
    assert proc.returncode == 1
    assert "error:" in proc.stderr.lower()


def test_verify_restore_bad_snapshot_returns_one(run_cli_fn, tmp_path):
    out = tmp_path / "restored"
    proc = run_cli_fn(
        "verify-restore",
        "--repo",
        _repo_arg(),
        "--target",
        str(out),
        "--snapshot",
        "not-a-real-snapshot",
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr.lower()


def test_verify_restore_corrupt_fixture_returns_one(run_cli_fn, tmp_path):
    # A fixture repo whose snapshot.json is corrupt (invalid JSON) must fail
    # cleanly: exit 1 with an `error:` on stderr, no traceback. This is the
    # "corrupt snapshot" half of the restore proof having teeth.
    repo = tmp_path / "repo-corrupt"
    shutil.copytree(FIXTURE_REPO, repo)
    (repo / "snapshot.json").write_text("{ not valid json", encoding="utf-8")

    out = tmp_path / "restored"
    proc = run_cli_fn(
        "verify-restore", "--repo", str(repo), "--target", str(out)
    )
    assert proc.returncode == 1, proc.stderr
    assert "error:" in proc.stderr.lower()
    # Nothing was restored.
    assert not out.exists()


def test_verify_restore_requires_repo_and_target(run_cli_fn):
    # Missing the required --target makes argparse exit 2.
    proc = run_cli_fn("verify-restore", "--repo", _repo_arg())
    assert proc.returncode == 2
