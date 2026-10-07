"""GitHub-ready contract tests.

The spec's "GitHub-ready contract" asserts that the repository ships with the
files and hygiene a fresh clone needs to be useful out of the box:

    README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
    `.gitignore`, and a full green test suite. Docs are authored — keep them in
    sync.

This module pins that contract with ``test_github_ready`` (and a small
docs-in-sync helper). The tests are file-system-local, need no network, root,
display, or hardware, and run as part of the normal `pytest` suite.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pengyos_license_audit

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_github_ready_contract():
    """The repo ships the files a fresh clone needs to be GitHub-ready."""
    # Required top-level files exist and are non-empty.
    for name in ("README.md", "CHANGELOG.md", ".gitignore"):
        path = REPO_ROOT / name
        assert path.exists(), f"missing {name}: repo not GitHub-ready"
        assert path.stat().st_size > 0, f"{name} is empty"

    # LICENSE is MIT and carries the required copyright line.
    license_text = (REPO_ROOT / "LICENSE").read_text()
    assert "MIT" in license_text, "LICENSE is not an MIT license"
    assert "Copyright (c) 2026 Pat Wendorf" in license_text

    # pyproject.toml is present, parses, and declares the project metadata.
    pyproject = REPO_ROOT / "pyproject.toml"
    assert pyproject.exists()
    data = tomllib.loads(pyproject.read_text())
    assert "name" in data["project"]
    assert "version" in data["project"]

    # The package imports cleanly and exposes its version — the precondition for
    # a green suite (the suite itself is enforced green by the loop's verifier).
    assert pengyos_license_audit.__version__
    from pengyos_license_audit import classify  # noqa: F401  # public API importable

    # The test suite is present (this contract test is part of it).
    test_files = list((REPO_ROOT / "tests").glob("test_*.py"))
    assert test_files, "no test files found under tests/"


def test_github_ready_docs_in_sync():
    """The version in pyproject.toml matches the package __version__."""
    pyproject = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["version"] == pengyos_license_audit.__version__
