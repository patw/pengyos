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

REPO = Path(__file__).resolve().parents[2]


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


# --- board post-build / post-image scripts -----------------------------------

def make_board_repo(tmp_path: Path, mode: int) -> Path:
    """A repo whose defconfig declares a post-build script, committed at `mode`."""
    repo = tmp_path / "repo"
    (repo / "buildroot-external/configs").mkdir(parents=True)
    (repo / "buildroot-external/board/desktop").mkdir(parents=True)
    (repo / "buildroot-external/board/common").mkdir(parents=True)
    (repo / "buildroot-external/board/desktop/post-build.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "buildroot-external/board/desktop/post-image.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "buildroot-external/board/common/image-ids.sh").write_text("VAR=1\n")  # sourced
    (repo / "buildroot-external/configs/pengyos_desktop_defconfig").write_text(
        'BR2_ROOTFS_POST_BUILD_SCRIPT="$(BR2_EXTERNAL_PENGYOS_PATH)/board/desktop/post-build.sh"\n'
        'BR2_ROOTFS_POST_IMAGE_SCRIPT="$(BR2_EXTERNAL_PENGYOS_PATH)/board/desktop/post-image.sh"\n'
        '# BR2_ROOTFS_POST_BUILD_SCRIPT: a comment must not be read as a script\n')
    for cmd in [["git", "init", "-q", str(repo)],
                ["git", "-C", str(repo), "config", "user.email", "t@t"],
                ["git", "-C", str(repo), "config", "user.name", "t"],
                ["git", "-C", str(repo), "add", "-A"],
                ["git", "-C", str(repo), "commit", "-q", "-m", "seed"]]:
        subprocess.run(cmd, check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "update-index",
                    f"--chmod={'-x' if mode == 0o644 else '+x'}",
                    "buildroot-external/board/desktop/post-build.sh"],
                   check=True, capture_output=True)
    return repo


def board_checks_for(repo: Path, needle: str) -> list:
    return [c for c in gate.check_board_script_executables(repo) if needle in c.name]


def test_the_declared_post_build_script_is_checked(tmp_path):
    repo = make_board_repo(tmp_path, 0o755)
    checks = board_checks_for(repo, "post-build.sh")
    assert checks, "the declared script was not checked at all"
    assert all(c.ok for c in checks), [c.detail for c in checks]


def test_a_post_build_script_that_lost_its_x_fails(tmp_path):
    """The 26.10.17 case: a commit rewrote post-build.sh at 100644 and the build
    host died at target-finalize with Permission denied."""
    repo = make_board_repo(tmp_path, 0o644)
    checks = board_checks_for(repo, "post-build.sh")
    assert checks and all(not c.ok for c in checks), [c.detail for c in checks]
    assert "100644" in checks[0].detail and "Permission denied" in checks[0].detail


def test_sourced_scripts_are_not_flagged(tmp_path):
    """image-ids.sh is `. source`d and /etc/profile.d/* is read by the shell; 0644
    is correct for them and a check that cries about both gets ignored."""
    repo = make_board_repo(tmp_path, 0o755)
    assert not board_checks_for(repo, "image-ids.sh")


def test_commented_out_declarations_are_not_read(tmp_path):
    repo = make_board_repo(tmp_path, 0o755)
    assert not board_checks_for(repo, "comment")


def test_negative_control_no_defconfigs(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    checks = gate.check_board_script_executables(repo)
    assert len(checks) == 1 and checks[0].ok, checks


def test_the_real_repo_declares_and_ships_its_board_scripts():
    """Against this repo: every defconfig-declared post-build/post-image script is
    found, and every one of them is executable in git."""
    scripts = gate._post_scripts(REPO)
    assert len(scripts) >= 4, scripts
    names = {s.rsplit("/", 1)[-1] for s in scripts}
    assert {"post-build.sh", "post-image.sh"} <= names, names
    bad = [c.detail for c in gate.check_board_script_executables(REPO) if not c.ok]
    assert not bad, bad
