"""Tests for the gate's conflict-marker check (scripts/pengyos_gate.py).

539a4c63 (2026-10-31) committed tools/README.md with a live conflict block; it sat on
main until a Pengy field review read the file on 2026-11-03.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pengyos_gate as gate  # noqa: E402


def make_repo(tmp_path: Path, files: dict) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, text in files.items():
        (repo / name).write_text(text)
    for cmd in [["git", "init", "-q", str(repo)],
                ["git", "-C", str(repo), "config", "user.email", "t@t"],
                ["git", "-C", str(repo), "config", "user.name", "t"],
                ["git", "-C", str(repo), "add", "-A"],
                ["git", "-C", str(repo), "commit", "-q", "-m", "seed"]]:
        subprocess.run(cmd, check=True, capture_output=True)
    return repo


def test_a_committed_conflict_block_fails(tmp_path):
    repo = make_repo(tmp_path, {"README.md": "| a |\n<<<<<<< HEAD\n| b |\n=======\n| c |\n>>>>>>> f0944b81 (x)\n"})
    [c] = gate.check_conflict_markers(repo)
    assert not c.ok and "README.md:2" in c.detail


def test_a_markdown_heading_rule_is_not_a_marker(tmp_path):
    repo = make_repo(tmp_path, {"README": "Title\n=======\ntext\n"})
    [c] = gate.check_conflict_markers(repo)
    assert c.ok, c.detail


def test_a_patch_may_quote_a_conflict(tmp_path):
    repo = make_repo(tmp_path, {"0001-x.patch": "+<<<<<<< HEAD\n<<<<<<< ours\n"})
    [c] = gate.check_conflict_markers(repo)
    assert c.ok, c.detail


def test_the_repo_itself_is_clean():
    [c] = gate.check_conflict_markers(Path(__file__).resolve().parents[2])
    assert c.ok, c.detail
