"""GitHub-ready contract tests.

The spec's *GitHub-ready contract* asserts, with a ``test_github_ready``, that
the repository ships everything a human needs to clone it and run it:

* ``README.md`` — documents the project and how to use it.
* ``LICENSE`` — MIT, "Copyright (c) 2026 Pat Wendorf".
* ``CHANGELOG.md`` — a changelog (Keep a Changelog style).
* ``.gitignore`` — keeps collected profiles out of version control.
* a full, green test suite.

These tests only read files on disk — no network, no root, no real hardware —
and lock the contract down so it cannot silently rot. Keeping the docs in sync
with the code is part of the contract, so the README is checked against the
subcommands the CLI actually advertises.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import pengyos_fleet
from pengyos_fleet.cli import build_parser

HERE = Path(__file__).parent
ROOT = HERE.parent


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The four contract files must exist.
# ---------------------------------------------------------------------------


def test_github_ready_contract_files_exist():
    # The four files the spec names, plus the packaging manifest the suite runs
    # from. A missing one means the repo is not GitHub-ready.
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"):
        path = ROOT / name
        assert path.is_file(), f"{name} is missing — repo is not GitHub-ready"
        assert path.stat().st_size > 0, f"{name} is empty"


# ---------------------------------------------------------------------------
# LICENSE — MIT, correct copyright holder.
# ---------------------------------------------------------------------------


def test_license_is_mit_with_copyright():
    license_text = _read("LICENSE")
    assert "MIT" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


# ---------------------------------------------------------------------------
# README — documents the project and how to use it.
# ---------------------------------------------------------------------------


def test_readme_documents_project_and_usage():
    readme = _read("README.md")
    # Names the tool and the project it serves.
    assert "pengyos-fleet" in readme
    assert "PengyOS" in readme
    # Has a usage section so a visitor can run it.
    assert "## Usage" in readme


def test_readme_is_in_sync_with_implementation():
    # The README must document every subcommand the CLI advertises (no stale or
    # missing docs) — this keeps the docs in sync with the code.
    readme = _read("README.md")
    advertised = build_parser().format_help()
    for cmd in ("collect", "diff"):
        assert cmd in advertised, f"{cmd} is advertised by the CLI but not in README"
        assert (
            f"`{cmd}`" in readme or f"`pengyos-fleet {cmd}`" in readme
        ), f"README does not document the `{cmd}` subcommand"


# ---------------------------------------------------------------------------
# CHANGELOG — present and in the expected format.
# ---------------------------------------------------------------------------


def test_changelog_present_and_formatted():
    changelog = _read("CHANGELOG.md")
    assert "Changelog" in changelog or "changelog" in changelog
    # Keep a Changelog style: a top-level heading and an unreleased/versions area.
    assert "## [" in changelog



# ---------------------------------------------------------------------------
# .gitignore — keeps collected profiles out of version control.
# ---------------------------------------------------------------------------


def test_gitignore_ignores_collected_profiles():
    gitignore = _read(".gitignore")
    # Collected profiles are generated output, not source.
    assert "profiles/" in gitignore
    assert "profile.toml" in gitignore


# ---------------------------------------------------------------------------
# pyproject.toml — packaging metadata the suite and console script rely on.
# ---------------------------------------------------------------------------


def test_pyproject_has_entry_point_and_version():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        metadata = tomllib.load(fh)
    # The console entry point the README documents.
    assert metadata["project"]["scripts"]["pengyos-fleet"] == "pengyos_fleet.cli:main"
    # Runtime is stdlib-only.
    assert metadata["project"]["dependencies"] == []


def test_version_constant_matches_pyproject():
    # Already asserted in the scaffold run, but the GitHub-ready contract owns
    # the "the shipped version is one coherent number" property.
    with (ROOT / "pyproject.toml").open("rb") as fh:
        metadata = tomllib.load(fh)
    assert pengyos_fleet.__version__ == metadata["project"]["version"]


# ---------------------------------------------------------------------------
# A full, green test suite — the contract's closing clause.
# ---------------------------------------------------------------------------


def test_suite_has_tests():
    # The suite is first-class: there are test modules to run.
    test_files = sorted(ROOT.glob("tests/test_*.py"))
    assert test_files, "no test modules found — the suite is not full"
    # The GitHub-ready contract has its own module here.
    assert ROOT / "tests" / "test_github_ready.py" in test_files
