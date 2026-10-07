"""Tests for the gate's overlay-executable check (scripts/pengyos_gate.py).

The check exists because 26.10.10 shipped /usr/libexec/pengyos/alsa-unity as 0644
(an editor rewrote the file and dropped the +x that afcefef8 had set) and
pengyos-alsa-unity.service died at boot with 203/EXEC - a green build and a green
gate, because nothing checked the mode. Reproduce both directions.

Run: python3 -m pytest scripts/tests -q   (from the repo root)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pengyos_gate as gate  # noqa: E402


def make_repo(tmp_path: Path, mode: int) -> Path:
    repo = tmp_path / "repo"
    ov = repo / "buildroot-external" / "board" / "desktop" / "rootfs-overlay"
    (ov / "etc/systemd/system").mkdir(parents=True)
    (ov / "usr/libexec/pengyos").mkdir(parents=True)
    script = ov / "usr/libexec/pengyos/alsa-unity"
    script.write_text("#!/bin/sh\nexit 0\n")
    os.chmod(script, mode)
    (ov / "etc/systemd/system/pengyos-alsa-unity.service").write_text(
        "[Service]\nExecStart=/usr/libexec/pengyos/alsa-unity\n")
    return repo


def checks_for(repo: Path, needle: str) -> list:
    return [c for c in gate.check_overlay_executables(repo) if needle in c.name]


def test_executable_script_passes(tmp_path):
    repo = make_repo(tmp_path, 0o755)
    checks = checks_for(repo, "alsa-unity")
    assert checks, "the ExecStart target was not checked at all"
    assert all(c.ok for c in checks)


def test_non_executable_script_fails(tmp_path):
    repo = make_repo(tmp_path, 0o644)
    checks = checks_for(repo, "alsa-unity")
    assert checks, "the ExecStart target was not checked at all"
    assert all(not c.ok for c in checks)
    assert "203/EXEC" in checks[0].detail


def test_negative_prefix_and_absent_target(tmp_path):
    # ExecStart=-/path (leading '-' = ignore failure) must still be checked, and a
    # target that is not an overlay file must be ignored, not flagged.
    repo = make_repo(tmp_path, 0o644)
    unit = repo / "buildroot-external/board/desktop/rootfs-overlay/etc/systemd/system/pengyos-alsa-unity.service"
    unit.write_text("[Service]\nExecStart=-/usr/libexec/pengyos/alsa-unity\n"
                    "ExecStartPre=/usr/bin/true\n")
    checks = checks_for(repo, "alsa-unity")
    assert checks and all(not c.ok for c in checks)
    assert not checks_for(repo, "/usr/bin/true")
