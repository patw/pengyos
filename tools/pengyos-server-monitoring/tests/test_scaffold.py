"""Scaffold verification for the PengyOS server-monitoring repo.

These tests assert the repository is well-formed and GitHub-ready at the point
the scaffold lands. They are stdlib-only and never touch the network, root, or
real hardware.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_pyproject_is_valid_and_well_formed() -> None:
    """pyproject.toml parses and carries the required project metadata."""
    data = tomllib.loads(_read(REPO_ROOT / "pyproject.toml"))

    project = data["project"]
    assert project["name"] == "pengyos-server-monitoring"
    assert project["version"] == "0.1.0"
    assert project["requires-python"].startswith(">=")
    # No runtime dependencies declared — stdlib only by design.
    assert project.get("dependencies", []) == []

    pytest_cfg = data.get("tool", {}).get("pytest", {}).get("ini_options")
    assert pytest_cfg is not None, "pytest must be configured in pyproject.toml"
    assert pytest_cfg.get("testpaths") == ["tests"]


def test_github_ready_contract_files_exist() -> None:
    """README, LICENSE, CHANGELOG and .gitignore are present and non-empty."""
    for name in ("README.md", "CHANGELOG.md", ".gitignore"):
        path = REPO_ROOT / name
        assert path.exists(), f"{name} is missing"
        assert path.stat().st_size > 0, f"{name} is empty"


def test_license_is_mit_and_attributed() -> None:
    """LICENSE is MIT and carries the required copyright attribution."""
    license_text = _read(REPO_ROOT / "LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


def test_gitignore_ignores_python_artifacts() -> None:
    """The .gitignore covers the artefacts pytest/Python produce locally."""
    gitignore = _read(REPO_ROOT / ".gitignore")
    for marker in ("__pycache__", "*.py[cod]", ".pytest_cache/"):
        assert marker in gitignore, f"{marker} should be ignored"


def test_package_importable() -> None:
    """The evaluator package imports cleanly (stdlib only)."""
    import pengyos_monitoring  # noqa: F401

    assert pengyos_monitoring.__version__ == "0.1.0"


def test_no_runtime_dependencies_beyond_test() -> None:
    """The only declared extras are test tooling."""
    data = tomllib.loads(_read(REPO_ROOT / "pyproject.toml"))
    extras = data["project"].get("optional-dependencies", {})
    assert set(extras) <= {"test"}
