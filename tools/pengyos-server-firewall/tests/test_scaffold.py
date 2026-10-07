"""Scaffold smoke test: the repo skeleton is in place and importable.

The full GitHub-ready contract is covered by test_github_ready; this test only pins the scaffold itself.
"""

import os
import re

import pytest

import pengyos_netctl

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REQUIRED_FILES = ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"]
REQUIRED_DIRS = ["pengyos_netctl", "nftables", "tests"]


@pytest.mark.parametrize("path", REQUIRED_FILES)
def test_scaffold_file_present(path):
    assert os.path.isfile(os.path.join(REPO_ROOT, path)), f"missing {path}"


@pytest.mark.parametrize("path", REQUIRED_DIRS)
def test_scaffold_dir_present(path):
    assert os.path.isdir(os.path.join(REPO_ROOT, path)), f"missing {path}/"


def test_package_imports_with_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", pengyos_netctl.__version__)


def test_license_is_mit_with_copyright():
    text = open(os.path.join(REPO_ROOT, "LICENSE"), encoding="utf-8").read()
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text
