"""GitHub-ready contract.

Asserts the repo ships the files PengyOS requires of every tool and that the
LICENSE carries the correct copyright line.  Run by ``pytest`` as part of the
suite; a failure here means the repo is not GitHub-ready.
"""

from __future__ import annotations

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rel(*parts: str) -> str:
    return os.path.join(REPO_ROOT, *parts)


@pytest.mark.parametrize(
    "path",
    ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"],
)
def test_required_files_exist(path: str) -> None:
    full = _rel(path)
    assert os.path.isfile(full), f"missing GitHub-ready file: {path}"
    assert os.path.getsize(full) > 0, f"empty GitHub-ready file: {path}"


def test_license_has_correct_copyright() -> None:
    license_text = open(_rel("LICENSE"), encoding="utf-8").read()
    assert "MIT" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


def test_changelog_has_header() -> None:
    changelog = open(_rel("CHANGELOG.md"), encoding="utf-8").read()
    assert re.match(r"^# ", changelog), "CHANGELOG should start with a level-1 heading"


def test_gitignore_ignores_python_artifacts() -> None:
    gitignore = open(_rel(".gitignore"), encoding="utf-8").read()
    assert "__pycache__" in gitignore
    assert "*.egg-info" in gitignore


def test_readme_mentions_the_tool() -> None:
    readme = open(_rel("README.md"), encoding="utf-8").read().lower()
    assert "pengyos-propose" in readme
    assert "gate" in readme
