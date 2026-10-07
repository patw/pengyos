"""Scaffold-level tests.

Covers the scaffold deliverable:

* ``pyproject.toml`` is valid PEP 621 metadata (name, version, MIT,
  ``setuptools`` backend, pytest configured to run ``tests/`` from the tool
  root);
* the ``tests/`` harness (``conftest.py`` path fix-up) is in place;
* the deliverable directory structure exists (``fragments/`` for the Buildroot
  fragment, ``shell/`` for the profile + tmux.conf + zoxide snippet;
  ``starship.toml`` / ``fzf.sh`` were dropped at item 7 — no Buildroot package);
* the GitHub-ready contract (README, LICENSE, CHANGELOG, .gitignore) is asserted
  in ``test_github_ready``.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
TESTS_DIR = REPO_ROOT / "tests"
FRAGMENTS_DIR = REPO_ROOT / "fragments"
SHELL_DIR = REPO_ROOT / "shell"


def _tracked_files(directory: Path) -> list[Path]:
    """Return the git-tracked files under ``directory`` (repo-root relative)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", str(directory)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / p for p in out.splitlines() if p.strip()]


# --- pyproject metadata -----------------------------------------------------


def test_pyproject_is_valid_pep621():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"]["name"] == "pengyos-server-toolset"
    assert data["project"]["version"]
    assert data["project"]["license"]["text"] == "MIT"
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"


def test_pyproject_configures_pytest_on_tests_dir():
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    pytest_cfg = data["tool"]["pytest"]["ini_options"]
    assert pytest_cfg["testpaths"] == ["tests"]
    assert pytest_cfg["pythonpath"] == ["."]


def test_pyproject_version_is_semver():
    version = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    parts = version.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_pyproject_is_stdlib_only_at_test_time():
    # No runtime deps declared; pytest is an optional test dependency.
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"]["dependencies"] == []
    assert "test" in data["project"]["optional-dependencies"]


# --- deliverable directory structure ----------------------------------------


def test_fragments_dir_exists():
    assert FRAGMENTS_DIR.is_dir(), "fragments/ directory must exist"


def test_shell_dir_exists():
    assert SHELL_DIR.is_dir(), "shell/ directory must exist"


def test_fragment_dir_has_at_least_one_tracked_file():
    # An empty directory is not tracked by git. The scaffold drops a .gitkeep
    # placeholder (item 1); the real fragment (item 2) will replace it, so this
    # asserts the directory has at least one tracked file (forward-compatible).
    tracked = _tracked_files(FRAGMENTS_DIR)
    assert tracked, (
        "fragments/ has no git-tracked files (an empty dir would not be "
        "tracked by git)"
    )


def test_shell_dir_has_at_least_one_tracked_file():
    tracked = _tracked_files(SHELL_DIR)
    assert tracked, (
        "shell/ has no git-tracked files (an empty dir would not be "
        "tracked by git)"
    )


# --- harness ----------------------------------------------------------------


def test_conftest_present():
    assert (TESTS_DIR / "conftest.py").is_file()


def test_conftest_fixes_up_sys_path():
    """The conftest must add the tool root to sys.path (import bootstrap)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "scaffold_conftest", TESTS_DIR / "conftest.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert str(REPO_ROOT) in mod.sys.path


def test_scaffold_test_modules_present():
    assert (TESTS_DIR / "test_scaffold.py").is_file()
    assert (TESTS_DIR / "test_github_ready.py").is_file()
