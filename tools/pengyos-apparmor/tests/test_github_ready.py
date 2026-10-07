"""GitHub-ready contract tests for the pengyos-apparmor repository.

Pins the contract from ``spec.md``: README.md, LICENSE (MIT,
"Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md, ``.gitignore``, and a
full green test suite. The file also verifies ``pyproject.toml`` is
consistent with the LICENSE/CHANGELOG/README it describes, that the
``.gitignore`` patterns are actually effective (``git check-ignore
--no-index``), and that the rest of the suite is green via a nested
``pytest --ignore=<this file>`` subprocess (no self-recursion: every
test here is named ``test_github_ready_*``).

Runs with no network, no root, and no AppArmor support.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml")

# Paths that must be ignored by .gitignore. Directory patterns are passed
# with a trailing slash: `git check-ignore` only matches a dir pattern like
# `build/` against a path that does not exist yet when the trailing slash
# is given.
IGNORED_EXAMPLES = (
    ".pytest_cache/",
    "__pycache__/",
    "*.egg-info/".replace("*.egg-info", "pkg.egg-info"),
    "profiles/pengy-agent.loaded",
)


def _git_check_ignore(*paths: str) -> subprocess.CompletedProcess | None:
    """Run `git check-ignore --no-index` on *paths*; None if git is unusable."""
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", *paths],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    if proc.returncode in (0, 1):
        return proc
    return None  # e.g. 128: not a git worktree — caller skips


def test_github_ready_required_files_exist_and_nonempty():
    for name in REQUIRED_FILES:
        path = ROOT / name
        assert path.is_file(), f"missing required file: {name}"
        assert path.stat().st_size > 0, f"required file is empty: {name}"


def test_github_ready_license_is_mit_with_pat_wendorf_2026():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.lstrip().startswith("MIT License")
    assert "Copyright (c) 2026 Pat Wendorf" in text
    # Standard MIT grant and no-warranty clauses.
    assert "Permission is hereby granted, free of charge" in text
    assert "THE SOFTWARE IS PROVIDED" in text
    assert "AS IS" in text


def test_github_ready_pyproject_is_consistent():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "pengyos-apparmor"
    # MIT in the metadata, matching LICENSE.
    assert project["license"] == {"text": "MIT"}
    assert "License :: OSI Approved :: MIT License" in project["classifiers"]
    # The readme field resolves to a real file.
    readme = project["readme"]
    assert (ROOT / readme).is_file()
    # The console script is declared and points at a real module.
    entry = project["scripts"]["pengyos-apparmor"]
    module_name, func = entry.split(":", 1)
    assert (ROOT / (module_name.replace(".", "/") + ".py")).is_file()
    assert func == "main"


def test_github_ready_changelog_carries_current_version():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = data["project"]["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{version}]" in changelog, (
        f"CHANGELOG.md does not carry the current version {version} "
        "(bump pyproject and CHANGELOG together)"
    )


def test_github_ready_readme_documents_the_interface():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    # The CLI subcommand and its exit-code contract.
    assert "pengyos-apparmor validate" in readme
    assert "--strict" in readme
    assert "Exit code" in readme
    # The testing command from spec.md.
    assert "pytest -q" in readme
    # The layout table names the real top-level dirs.
    for path in ("profiles/", "pengyos_apparmor/", "tests/"):
        assert path in readme
        assert (ROOT / path).exists()
    # The LICENSE link target resolves.
    assert "[LICENSE](LICENSE)" in readme


def test_github_ready_gitignore_is_effective():
    proc = _git_check_ignore(*IGNORED_EXAMPLES)
    if proc is None:
        pytest.skip("not a git worktree — check-ignore cannot run")
    assert proc.returncode == 0, (
        f".gitignore does not cover {IGNORED_EXAMPLES}:\n{proc.stderr}"
    )
    # And the required files themselves are NOT ignored.
    proc = _git_check_ignore(*REQUIRED_FILES)
    if proc is None:
        pytest.skip("not a git worktree — check-ignore cannot run")
    assert proc.returncode == 1, (
        "a required file is git-ignored and would not survive a clone:\n"
        + proc.stdout
    )


def test_github_ready_full_suite_is_green():
    """The rest of the suite must be green (this file is excluded so the
    nested run terminates)."""
    env = {k: v for k, v in os.environ.items() if k != "PENGYOS_IT"}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            f"--ignore={Path(__file__).relative_to(ROOT)}",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=300,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"test suite not green:\n{output}"
    # The -q summary line (e.g. "90 passed, 2 skipped in 1.2s") must report
    # only passes/skips/xpasses/xfails.
    summary = output.strip().splitlines()[-1]
    assert " passed" in summary, f"no pass summary line: {summary!r}"
    assert " failed" not in summary
    assert " error" not in summary
