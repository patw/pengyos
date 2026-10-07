"""GitHub-ready contract for the pengyos-recipe-lint repo.

``spec.md`` defines a **GitHub-ready contract** that must be asserted with a
``test_github_ready``::

    README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
    `.gitignore`, and a full green test suite. Docs are authored — keep them in
    sync.

This module asserts the file artifacts (the suite's own greenness is the verify
step's job — nothing here re-invokes pytest) and that the packaged deliverable
imports and exposes its CLI entry point, so a freshly-cloned checkout is
publishable and runnable before a single rule is even exercised.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
import tomllib

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


def _require(name: str) -> Path:
    """Return a present, non-empty repo artifact, failing loudly otherwise."""
    path = ROOT / name
    assert path.is_file(), f"GitHub-ready artifact missing: {name}"
    assert path.read_text(encoding="utf-8").strip(), f"{name} is empty"
    return path


def test_github_ready():
    """The repo ships the GitHub-ready artifacts with correct metadata.

    Asserts the contract from ``spec.md``: README, LICENSE (MIT, credited to
    Pat Wendorf), CHANGELOG and ``.gitignore`` all authored and non-empty, plus
    ``pyproject.toml`` that wires the ``pengyos-recipe-lint`` console script.
    """
    # README documents the tool (not a stub) and stays in sync with the spec.
    readme = _require("README.md").read_text(encoding="utf-8")
    assert "pengyos-recipe-lint" in readme.lower()

    # LICENSE is MIT and credits the right holder — every time.
    license_text = _require("LICENSE").read_text(encoding="utf-8")
    assert "MIT" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text

    # CHANGELOG and .gitignore are authored.
    _require("CHANGELOG.md")
    _require(".gitignore")

    # Package metadata is present and exposes the documented console script.
    with (ROOT / "pyproject.toml").open("rb") as fh:
        meta = tomllib.load(fh)
    scripts = meta["project"]["scripts"]
    assert scripts == {
        "pengyos-recipe-lint": "pengyos_recipe_lint.cli:main",
    }
    # The documented runtime constraint holds.
    assert meta["project"]["requires-python"] == ">=3.11"


def test_github_ready_package_imports():
    """The packaged deliverable imports and exposes its CLI entry point."""
    cli = importlib.import_module("pengyos_recipe_lint.cli")
    assert callable(cli.main)


def test_github_ready_cli_runs_without_install():
    """`python -m pengyos_recipe_lint` works from a clean checkout (no install).

    The README documents running without installing; this smoke test pins that
    promise so the entry point can't silently rot.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_recipe_lint", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pengyos-recipe-lint" in proc.stdout
