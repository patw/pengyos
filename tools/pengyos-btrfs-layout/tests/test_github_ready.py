"""GitHub-ready contract (spec.md, "GitHub-ready contract").

Asserts that the repository is publishable to GitHub as-is:

- ``README.md``, ``LICENSE`` (MIT, "Copyright (c) 2026 Pat Wendorf"),
  ``CHANGELOG.md`` and ``.gitignore`` are present and correct;
- the docs are in sync with ``spec.md`` (the CLI usage lines in the README
  match the spec's Interface section verbatim);
- the full test suite is green (re-run in a subprocess, excluding this
  file so the re-run cannot recurse).

Every test in this file is named ``test_github_ready_*`` on purpose: the
nested re-run filters ``-k "not test_github_ready"``, which matches exactly
this file and nothing else.

No network, no root, no external tools — file reads, one ``git check-ignore``
and one ``pytest`` subprocess against the repo itself.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    path = REPO / name
    assert path.is_file(), f"missing repo file: {name}"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{name} is empty"
    return text


def test_github_ready_required_files_exist():
    for name in (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        ".gitignore",
        "pyproject.toml",
        "spec.md",
        "conftest.py",
    ):
        _read(name)


def test_github_ready_license_is_mit_with_copyright():
    license_text = _read("LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text
    assert "WITHOUT WARRANTY" in license_text

    readme = _read("README.md")
    assert "MIT" in readme
    assert "Copyright (c) 2026 Pat Wendorf" in readme


def test_github_ready_changelog_maintained():
    changelog = _read("CHANGELOG.md")
    assert "Keep a Changelog" in changelog
    assert "## [Unreleased]" in changelog
    assert "## [0.1.0]" in changelog


def test_github_ready_pyproject_is_publishable():
    pyproject = _read("pyproject.toml")
    assert 'name = "pengyos-btrfs-layout"' in pyproject
    assert 'readme = "README.md"' in pyproject
    assert "MIT" in pyproject
    assert 'pengyos-btrfs = "pengyos_btrfs.cli:main"' in pyproject
    # Standard library only: no runtime dependencies declared.
    assert "dependencies = []" in pyproject


def test_github_ready_gitignore_is_effective():
    gitignore_lines = _read(".gitignore").splitlines()
    for pattern in (
        "__pycache__/",
        "*.py[cod]",
        "*.egg-info/",
        ".pytest_cache/",
        "dist/",
        ".venv/",
    ):
        assert any(line.strip() == pattern for line in gitignore_lines), (
            f".gitignore does not ignore {pattern!r}"
        )

    # The ignore rules must actually be effective in this git repo.
    for path in ("__pycache__/", ".pytest_cache/", "build/", "dist/"):
        result = subprocess.run(
            ["git", "-C", str(REPO), "check-ignore", "--no-index", path],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"git does not ignore {path!r} (check-ignore exit "
            f"{result.returncode}): {result.stderr.strip()}"
        )


def _spec_interface_lines() -> list:
    """The ``pengyos-btrfs <cmd> ...`` lines under spec.md's Interface."""
    lines = []
    in_interface = False
    for line in _read("spec.md").splitlines():
        if line.startswith("## "):
            in_interface = line.startswith("## Interface")
            continue
        if in_interface and line.startswith("- pengyos-btrfs "):
            lines.append(line[len("- "):].strip())
    return lines


def test_github_ready_readme_in_sync_with_spec():
    interface_lines = _spec_interface_lines()
    assert interface_lines, (
        "no interface commands found in spec.md's Interface section"
    )

    readme = _read("README.md")
    for line in interface_lines:
        assert line in readme, (
            f"README.md is out of sync with spec.md's Interface — missing or "
            f"reworded usage line:\n    {line}"
        )


def test_github_ready_full_test_suite_green():
    # Re-run the whole suite, excluding exactly this file (its tests all
    # carry the ``test_github_ready`` name) so the re-run terminates.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-k",
            "not test_github_ready",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = (result.stdout + "\n" + result.stderr).strip()
    assert result.returncode == 0, (
        "full test suite is not green (inner `pytest -k 'not "
        "test_github_ready'` failed):\n" + tail[-4000:]
    )
    last_line = result.stdout.strip().splitlines()[-1]
    assert " passed" in last_line, (
        f"inner pytest run did not report passing tests: {last_line}"
    )
