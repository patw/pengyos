"""GitHub-ready contract.

The PengyOS maintenance loop only *ships* a tool that is presentable: a green
test suite plus the standing documentation that makes a clone useful the moment
it is checked out. :func:`test_github_ready` asserts that contract in one
place — every file that must exist, and every invariant that must hold — and
raises a single consolidated report if any piece is missing so a reviewer sees
 the whole picture at once.

The opt-in full-suite gate (``PENGYOS_IT=1``) lives in ``tests/test_scaffold.py``
and re-runs the whole suite in a subprocess to prove it is actually *green*;
``test_github_ready`` itself asserts the static contract.

Standard library only; no network, root, display, or hardware.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The standing documentation a repo must carry to be GitHub-ready.
_DOCUMENTS = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore")

# The exact copyright line the spec pins for the MIT LICENSE.
_COPYRIGHT = "Copyright (c) 2026 Pat Wendorf"


def _read(name: str) -> str:
    """Read a repo file, returning ``""`` when it is absent so the contract
    helpers can report *every* missing piece at once instead of crashing on
    the first missing file."""
    path = REPO_ROOT / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _assert_documents_present() -> None:
    for name in _DOCUMENTS:
        text = _read(name)
        if not text:
            raise AssertionError(f"missing GitHub-ready file: {name}")
        if not text.strip():
            raise AssertionError(f"empty GitHub-ready file: {name}")


def _assert_license() -> None:
    text = _read("LICENSE")
    assert text, "LICENSE is missing"
    assert "MIT" in text, "LICENSE must be an MIT license"
    assert _COPYRIGHT in text, (
        f"LICENSE must carry {_COPYRIGHT!r}; found:\n{text[:200]}"
    )


def _assert_readme() -> None:
    text = _read("README.md")
    assert text, "README.md is missing"
    # The project is named and its license is stated, so a fresh clone knows
    # what it is and how it may be used.
    assert "pengyos-upstream-watch" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text


def _assert_changelog() -> None:
    text = _read("CHANGELOG.md")
    assert text, "CHANGELOG.md is missing"
    # Keep a Changelog: release sections are headed ``## [x.y.z]`` (or
    # ``## [Unreleased]``). At least one must be present.
    assert re.search(r"^## \[(Unreleased|\d+\.\d+\.\d+)]", text, re.MULTILINE), (
        "CHANGELOG.md must follow Keep a Changelog release headings"
    )


def _assert_gitignore() -> None:
    # The emitted proposal document is a build output and must never be
    # committed.
    text = _read(".gitignore")
    assert text, ".gitignore is missing"
    assert "proposals.json" in text, ".gitignore must ignore proposals.json"


def _assert_build_metadata() -> None:
    text = _read("pyproject.toml")
    assert text, "pyproject.toml is missing"
    try:
        meta = tomllib.loads(text)
        project = meta["project"]
        name = project["name"]
        license_ = project["license"]
        scripts = project["scripts"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise AssertionError(f"pyproject.toml is incomplete or malformed: {exc}")
    assert name == "pengyos-upstream-watch"
    assert license_ == {"text": "MIT"}
    # The console script is the documented entry point.
    assert scripts["pengyos-upstream-watch"] == "pengyos_upstream_watch.cli:main"


def _assert_package_coherent() -> None:
    import pengyos_upstream_watch as pkg

    assert isinstance(pkg.__version__, str)
    # __version__ must not drift from the declared build metadata.
    text = _read("pyproject.toml")
    assert text, "pyproject.toml is missing"
    try:
        project = tomllib.loads(text)["project"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise AssertionError(f"pyproject.toml is incomplete or malformed: {exc}")
    assert pkg.__version__ == project["version"]


def test_github_ready():
    """The whole GitHub-ready contract, asserted in one entry point.

    Every failing check contributes a line to a single consolidated report so a
    reviewer sees the whole picture at once rather than fixing one failure,
    running, and discovering the next.
    """
    problems: list[str] = []

    def _record(exc: AssertionError) -> None:
        message = str(exc)
        if message not in problems:
            problems.append(message)

    for check in (
        _assert_documents_present,
        _assert_license,
        _assert_readme,
        _assert_changelog,
        _assert_gitignore,
        _assert_build_metadata,
        _assert_package_coherent,
    ):
        try:
            check()
        except AssertionError as exc:
            _record(exc)

    assert not problems, (
        "GitHub-ready contract not met:\n  - " + "\n  - ".join(problems)
    )


def test_github_ready_contract_is_documented_in_spec() -> None:
    """The spec records the contract so it is not a hidden convention."""
    spec = (REPO_ROOT / "spec.md").read_text(encoding="utf-8")
    assert "GitHub-ready contract" in spec
    assert "test_github_ready" in spec


def test_repo_tree_has_no_tracked_ignored_artifacts() -> None:
    """Nothing the tool ignores may also be tracked by git. The tool lives
    under ``tools/pengyos-upstream-watch`` of the PengyOS monorepo, so ``.git``
    can be an ancestor, not the repo root."""
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip("not inside a git work tree")
    proc = subprocess.run(
        ["git", "ls-files", "--cached"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = proc.stdout.splitlines()
    offenders = [
        name
        for name in tracked
        if name.endswith((".pyc", ".egg-info")) or "__pycache__" in name
        or ".pytest_cache" in name
    ]
    assert not offenders, f"gitignored artifacts are tracked: {offenders}"


if __name__ == "__main__":
    # Test modules are collected by pytest, not run as scripts; this keeps the
    # file importable under either mode without a dangling name.
    print("run this suite with: pytest -q")
    sys.exit(0)
