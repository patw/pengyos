"""GitHub-ready contract (spec.md "Verification").

The repo must ship the required docs/metadata files AND have a full green
test suite. ``test_github_ready`` is the single unified assertion a reviewer
greps for; the per-file checks in ``tests/test_scaffold.py`` stay as the
focused unit tests, this module adds the unified gate and the green-suite
proof.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# The tool directory (parent of tests/).
TOOL_DIR = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    path = TOOL_DIR / name
    assert path.is_file(), f"missing GitHub-required file: {name}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{name} is empty"
    return text


def test_github_ready():
    """README.md, MIT LICENSE, CHANGELOG.md, .gitignore — and a green suite."""

    # 1. README.md — authored and about this project, documents the CLI
    #    interface from spec.md, and links the license.
    readme = _read("README.md")
    assert "pengyos-initramfs" in readme
    assert "pengyos-initramfs build --root DIR --spec initramfs.toml --out init.cpio.gz" in readme
    assert "LICENSE" in readme
    assert "Copyright (c) 2026 Pat Wendorf" in readme

    # 2. LICENSE — MIT, with the exact required copyright line.
    license_text = _read("LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text

    # 3. CHANGELOG.md — authored changelog with a released section.
    changelog = _read("CHANGELOG.md")
    assert "# Changelog" in changelog
    assert "0.1.0" in changelog

    # 4. .gitignore — present and ignores the obvious build/test noise.
    gitignore = _read(".gitignore")
    assert "__pycache__/" in gitignore

    # 5. A full green test suite. Run the whole suite in a fresh subprocess,
    #    excluding this self-referential test to avoid infinite recursion.
    #    A non-zero return code (any failure or error) means the repo is not
    #    GitHub-ready; surface a tail of the output for debuggability.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-k", "not test_github_ready"],
        cwd=str(TOOL_DIR),
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-2000:]
    assert proc.returncode == 0, (
        f"GitHub-ready check failed: test suite is not green "
        f"(return code {proc.returncode})\n{tail}"
    )
