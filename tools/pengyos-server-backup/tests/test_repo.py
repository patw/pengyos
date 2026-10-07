"""Repo checks: the files that must exist for a GitHub-ready repo.

The per-file checks stay as focused unit tests; ``test_github_ready`` is the
single unified assertion a reviewer greps for (spec.md Verification).

A GitHub-ready repo is more than "the four files exist": the docs must be
*authored* (not stubs), the LICENSE must be a *canonical* MIT notice, the
CHANGELOG must track in-flight work with a dated release, the shipped version
must be *consistent* across ``__init__`` / ``pyproject.toml`` / the docs, and a
cloning reviewer must be able to run the CLI from a fresh checkout. Each of
those is its own test; ``test_github_ready`` composes them.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from pengyos_backup import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_present_and_valid():
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file()
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["name"] == "pengyos-backup"
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"
    assert data["project"]["scripts"]["pengyos-backup"].endswith("cli:main")
    # The declared project version is present (consumed by the CLI __version__).
    assert data["project"]["version"] == __version__


def test_readme_authored():
    # A GitHub README must be authored, not a stub: it documents the project,
    # links the LICENSE, and advertises the three subcommands.
    readme = (ROOT / "README.md").read_text()
    assert len(readme) > 1000, "README.md looks like a stub"
    assert "pengyos-backup" in readme
    assert "LICENSE" in readme  # links the license
    for command in ("plan", "verify-restore", "render"):
        assert command in readme, (
            f"README does not document the {command} subcommand"
        )


def test_license_is_full_mit():
    # The LICENSE is a canonical MIT notice: the header, the exact copyright
    # line, and the standard permission/warranty body.
    text = (ROOT / "LICENSE").read_text()
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text
    # The canonical MIT permission notice (the part that makes it MIT).
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text
    assert "OR OTHERWISE" in text


def test_changelog_recent():
    # Keep a Changelog: an [Unreleased] section for in-flight work plus a
    # dated [0.1.0] release section.
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "# Changelog" in text
    assert "[Unreleased]" in text
    assert "0.1.0" in text


def test_gitignore_covers_noise():
    text = (ROOT / ".gitignore").read_text()
    assert "__pycache__/" in text
    # pytest cache and emitted systemd units are also noise.
    assert ".pytest_cache/" in text
    assert "/units/" in text


def test_package_layout():
    for name in ("__init__.py", "cli.py", "__main__.py"):
        assert (ROOT / "pengyos_backup" / name).is_file()


def test_version_consistent():
    # The package version is the single source of truth and must agree with
    # pyproject and show up in the shipped docs.
    assert __version__ == "0.1.0"
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__
    assert __version__ in (ROOT / "README.md").read_text()
    assert __version__ in (ROOT / "CHANGELOG.md").read_text()


def test_cli_help_runs_in_fresh_subprocess(root):
    # A reviewer cloning the repo must be able to run the CLI immediately,
    # from a checkout, without an editable install.
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_backup", "--help"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    for command in ("plan", "verify-restore", "render", "version"):
        assert command in proc.stdout


# GitHub-ready contract (spec.md Verification): the repo must ship the
# required docs/metadata files AND have a full green test suite.


def _read(name: str) -> str:
    path = ROOT / name
    assert path.is_file(), f"missing GitHub-required file: {name}"
    return path.read_text()


def test_github_ready():
    # 1. README.md — authored and about this project.
    readme = _read("README.md")
    assert "pengyos-backup" in readme
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
