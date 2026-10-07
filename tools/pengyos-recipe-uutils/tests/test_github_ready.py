"""GitHub-ready contract tests for the PengyOS uutils-coreutils repo.

The spec's "GitHub-ready contract" (see ``spec.md``)
asserts, with a single ``test_github_ready`` marker, that the repo is
push-ready:

* ``README.md`` is authored and non-empty,
* ``LICENSE`` is the MIT license carrying the required copyright line
  (``Copyright (c) 2026 Pat Wendorf``),
* ``CHANGELOG.md`` follows Keep-a-Changelog,
* ``.gitignore`` is present and non-empty, and
* the test suite itself is green.

The last point is the only self-referential one. We verify it by running the
suite **in a subprocess** (a fresh interpreter, so nested collection cannot
recurse) guarded by a unique environment variable. If the guard is already set
we are inside a nested run and we skip the subprocess check to avoid infinite
recursion — the metadata contract is still asserted in that pass.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Recursion guard for the self-green check (see test_github_ready).
_GREEN_RUN_GUARD = "PENGYOS_GITHUB_READY_RUN"

# The metadata files that make the repo push-ready.
GITHUB_READY_FILES = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore")

# The exact copyright line the spec mandates on the MIT LICENSE.
REQUIRED_COPYRIGHT = "Copyright (c) 2026 Pat Wendorf"


def _run_suite_in_subprocess() -> int:
    """Run the full suite in a fresh interpreter and return its exit code.

    The subprocess inherits our environment but with the recursion guard set,
    so its own ``test_github_ready`` will not spawn a further subprocess.
    """
    env = dict(os.environ)
    env[_GREEN_RUN_GUARD] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    return result.returncode


def _assert_repo_file(name: str) -> Path:
    path = REPO_ROOT / name
    assert path.is_file(), f"missing repo file: {name}"
    assert path.read_text(encoding="utf-8").strip(), f"{name} is empty"
    return path


def test_github_ready_files_present() -> None:
    for name in GITHUB_READY_FILES:
        _assert_repo_file(name)


def test_license_is_mit_with_required_copyright() -> None:
    """LICENSE must be MIT and carry the mandated copyright line."""
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT" in license_text, "LICENSE does not declare the MIT license"
    assert REQUIRED_COPYRIGHT in license_text, (
        f"LICENSE must contain {REQUIRED_COPYRIGHT!r}"
    )


def test_changelog_follows_keep_a_changelog() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in changelog or "## [" in changelog, (
        "CHANGELOG.md should follow Keep-a-Changelog (## Unreleased or ## [x.y.z])"
    )


def test_readme_documents_the_recipe() -> None:
    """README should be authored and mention this repo's purpose."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "uutils" in readme.lower(), "README should mention uutils"
    assert "recipe" in readme.lower(), "README should describe the recipe"


def test_gitignore_tracks_build_and_test_artifacts() -> None:
    """The .gitignore should keep the repo clean of cache/bytecode noise."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "__pycache__" in gitignore, ".gitignore should ignore __pycache__"
    assert ".pytest_cache" in gitignore, ".gitignore should ignore .pytest_cache"


def test_github_ready() -> None:
    """The whole suite must be green (self-check, recursion-guarded)."""
    # The metadata contract is asserted by the dedicated tests above; this run
    # additionally proves the suite is green by re-running it in a subprocess.
    # Skip only when we are already nested (the guard is set) to prevent
    # infinite recursion.
    if os.environ.get(_GREEN_RUN_GUARD):
        return

    code = _run_suite_in_subprocess()
    assert code == 0, (
        f"test suite is not green (exit {code}); GitHub-ready contract violated"
    )
