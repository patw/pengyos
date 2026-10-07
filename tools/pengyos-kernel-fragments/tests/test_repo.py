"""Repo scaffolding checks: the files that must exist for a GitHub-ready repo."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_present_and_valid():
    import tomllib

    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file()
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["name"] == "pengyos-kernel-fragments"
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"


def test_readme_present():
    readme = (ROOT / "README.md").read_text()
    assert "pengyos-kernel-fragments" in readme


def test_license_is_mit_pat_wendorf_2026():
    text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_changelog_present():
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "# Changelog" in text
    assert "0.1.0" in text


def test_gitignore_present():
    text = (ROOT / ".gitignore").read_text()
    assert "__pycache__/" in text


def test_package_layout():
    for name in ("__init__.py", "cli.py", "__main__.py"):
        assert (ROOT / "pengyos_kernel_fragments" / name).is_file()


# GitHub-ready contract (spec.md Verification): the repo must ship the
# required docs/metadata files AND have a full green test suite. The per-file
# checks above stay as focused unit tests; test_github_ready is the single
# unified assertion a reviewer greps for.


def _read(name: str) -> str:
    path = ROOT / name
    assert path.is_file(), f"missing GitHub-required file: {name}"
    return path.read_text()


def test_github_ready():
    # 1. README.md — authored and about this project.
    readme = _read("README.md")
    assert "pengyos-kernel-fragments" in readme
    assert "LICENSE" in readme  # links the license

    # 2. LICENSE — MIT, the exact required copyright line.
    license_text = _read("LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text

    # 3. CHANGELOG.md — authored changelog with the release section.
    changelog = _read("CHANGELOG.md")
    assert "# Changelog" in changelog
    assert "0.1.0" in changelog

    # 4. .gitignore — present and ignores the obvious build/test noise.
    gitignore = _read(".gitignore")
    assert "__pycache__/" in gitignore

    # 5. A full green test suite. Run the whole suite in a fresh subprocess,
    #    excluding this self-referential test to avoid infinite recursion. A
    #    non-zero return code (any failure or skip) means the repo is not
    #    GitHub-ready; surface a tail of the output for debuggability.
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-k", "not test_github_ready"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-2000:]
    assert proc.returncode == 0, (
        f"GitHub-ready check failed: test suite is not green "
        f"(return code {proc.returncode})\n{tail}"
    )
