"""Tests for the gate's script-exec check (scripts/pengyos_gate.py).

The check exists because `scripts/build-cosmic.sh` sat at git mode 100644 while
`scripts/release.sh` calls it directly (line 92), so a human running release.sh
without --skip-cosmic died with `Permission denied` at the COSMIC step - found
2026-10-08 while validating an NVIDIA driver bump. The nightly never hit it because
the act lane always passes --skip-cosmic. Same class as the release.sh exec-bit loss
(commit 2d894fba) that cost a night in pengyos-nightly 0.11.1.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pengyos_gate as gate  # noqa: E402


def make_repo(tmp_path: Path, mode: int) -> Path:
    """A repo with one shebang'd script, committed at `mode`."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    callee = repo / "scripts" / "build-cosmic.sh"
    callee.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(callee, mode)
    caller = repo / "scripts" / "release.sh"
    caller.write_text("#!/bin/sh\n\"${REPO}/scripts/build-cosmic.sh\"\n")
    os.chmod(caller, 0o755)
    for cmd in [["git", "init", "-q", str(repo)],
                ["git", "-C", str(repo), "config", "user.email", "t@t"],
                ["git", "-C", str(repo), "config", "user.name", "t"],
                ["git", "-C", str(repo), "add", "-A"],
                ["git", "-C", str(repo), "commit", "-q", "-m", "seed"]]:
        subprocess.run(cmd, check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "update-index",
                    f"--chmod={'-x' if mode == 0o644 else '+x'}", "scripts/build-cosmic.sh"],
                   check=True, capture_output=True)
    return repo


def checks_for(repo: Path, needle: str) -> list:
    return [c for c in gate.check_script_executables(repo) if needle in c.name]


def test_executable_script_passes(tmp_path):
    repo = make_repo(tmp_path, 0o755)
    checks = checks_for(repo, "build-cosmic.sh")
    assert checks, "the script was not checked at all"
    assert all(c.ok for c in checks), [c.detail for c in checks]


def test_non_executable_script_fails(tmp_path):
    repo = make_repo(tmp_path, 0o644)
    checks = checks_for(repo, "build-cosmic.sh")
    assert checks and all(not c.ok for c in checks), [c.detail for c in checks]
    assert "100644" in checks[0].detail and "Permission denied" in checks[0].detail


def test_the_git_mode_is_what_counts_not_the_disk(tmp_path):
    """A +x on this disk with 100644 in the index is still broken for a fresh clone."""
    repo = make_repo(tmp_path, 0o644)
    os.chmod(repo / "scripts" / "build-cosmic.sh", 0o755)      # disk says yes...
    assert os.access(repo / "scripts" / "build-cosmic.sh", os.X_OK)
    checks = checks_for(repo, "build-cosmic.sh")
    assert all(not c.ok for c in checks), "the index mode is the one a clone gets"


def test_a_script_without_a_shebang_is_not_flagged(tmp_path):
    repo = make_repo(tmp_path, 0o755)
    data = repo / "scripts" / "cosmic-components.tsv"
    data.write_text("name\trepo\n")
    os.chmod(data, 0o644)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    assert not checks_for(repo, "cosmic-components.tsv")


def test_negative_control_no_scripts_dir(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    checks = gate.check_script_executables(repo)
    assert len(checks) == 1 and checks[0].ok, checks
