"""GitHub-ready contract.

Per ``spec.md`` the project must stay GitHub-ready at all times, which means these
repo files exist, are authored, and are kept in sync:

* ``README.md`` — project overview + usage.
* ``LICENSE`` — MIT, "Copyright (c) 2026 Pat Wendorf".
* ``CHANGELOG.md`` — a changelog (Keep a Changelog shaped).
* ``.gitignore`` — ignores the generated ``profile.toml``.
* ``pyproject.toml`` — valid packaging metadata with the console entry point.

and the whole test suite is green. This module asserts the file/metadata half of
that contract; the "green suite" half is the acceptance criterion that runs
``pytest`` over everything.

Nothing here needs the network, root, a real display, or real hardware — it only
reads committed repo files.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pengyos_hwprofile

# The repository root (the parent of this ``tests/`` directory). Everything is
# referenced relative to it so the test is independent of the invocation cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]


def _repo_file(name: str) -> Path:
    """A repo-root file, failing with a clear message if it is missing."""

    path = REPO_ROOT / name
    assert path.is_file(), f"repo-ready contract: {name} is missing"
    return path


def test_readme_is_authored():
    text = _repo_file("README.md").read_text(encoding="utf-8")
    assert text.strip(), "README.md is empty"
    # It is the project's entry doc: it names the tool and PengyOS.
    assert "pengyos-hwprofile" in text
    assert "PengyOS" in text
    # It documents how to use the tool, not just that it exists.
    assert "Usage" in text or "usage" in text


def test_license_is_mit_with_correct_copyright():
    text = _repo_file("LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    # The exact copyright line the contract pins.
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_changelog_is_authored():
    text = _repo_file("CHANGELOG.md").read_text(encoding="utf-8")
    assert text.strip(), "CHANGELOG.md is empty"
    assert "Changelog" in text
    # A real changelog records released versions.
    assert "[0." in text


def test_gitignore_ignores_generated_profile():
    text = _repo_file(".gitignore").read_text(encoding="utf-8")
    # The generated profile.toml is not source; it must be ignored.
    assert "profile.toml" in text


def test_pyproject_is_valid_and_named():
    path = _repo_file("pyproject.toml")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    # Runtime is stdlib-only; nothing to declare.
    assert data["project"]["dependencies"] == []
    # The console entry point exists and points at the CLI.
    scripts = data["project"]["scripts"]
    assert "pengyos-hwprofile" in scripts
    assert scripts["pengyos-hwprofile"] == "pengyos_hwprofile.cli:main"


def test_version_is_in_sync_across_docs():
    # pyproject.toml and the package __version__ must agree (kept in sync on
    # purpose; this is what "docs authored — keep them in sync" enforces).
    data = tomllib.loads(
        (_repo_file("pyproject.toml")).read_text(encoding="utf-8")
    )
    pyproject_version = data["project"]["version"]
    package_version = pengyos_hwprofile.__version__
    assert pyproject_version == package_version, (
        f"version drift: pyproject={pyproject_version!r} "
        f"but pengyos_hwprofile.__version__={package_version!r}"
    )
    # And it is a real semantic version.
    assert package_version.count(".") >= 2
    assert package_version.split(".")[0].isdigit()


def test_suite_has_tests():
    # The "full green test suite" is only meaningful if there are tests to run.
    test_files = sorted(REPO_ROOT.glob("tests/test_*.py"))
    assert test_files, "no test modules found under tests/"
