"""GitHub-ready contract tests.

The spec's *GitHub-ready contract* says the repository must, at all times:

* ship an authored ``README.md`` (install, usage, exit codes, license line),
* ship a ``LICENSE`` that is MIT with "Copyright (c) 2026 Pat Wendorf",
* ship a ``CHANGELOG.md`` in Keep a Changelog format, current with the
  package version,
* ship a ``.gitignore`` that keeps build/test noise and tool runtime
  artifacts out of the tree,
* have a full green test suite, and
* keep the docs **in sync** with the code.

Design notes:

* The in-suite tests below are FAST. They assert file presence, content, and
  docs/code sync. They do **not** re-run the test suite from inside itself
  (that would be quadratic and fragile) — the "full green test suite" is the
  outer ``pytest -q`` gate that this file is part of.
* The single exception is an opt-in integration test behind ``PENGYOS_IT=1``
  that re-runs the suite in a subprocess and asserts it exits 0. The child
  environment has ``PENGYOS_IT`` *removed* so the run cannot recurse.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pengyos_appimage import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "src" / "pengyos_appimage"
TESTS_DIR = REPO_ROOT / "tests"
README = REPO_ROOT / "README.md"
LICENSE = REPO_ROOT / "LICENSE"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYTHONIGNORE = REPO_ROOT / ".gitignore"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing required GitHub-ready file: {path.name}"
    return path.read_text(encoding="utf-8")


def _version_headings(text: str):
    """Every ``## [X.Y.Z] <sep> YYYY-MM-DD`` heading, topmost first.

    The separator is a hyphen (Keep a Changelog canonical) or an em dash
    (a common, accepted variant — the committed CHANGELOG uses the latter).
    """
    return re.findall(r"^## \[([^\]]+)\] [—-] \d{4}-\d{2}-\d{2}$", text, re.M)


# --- README.md ---------------------------------------------------------------


def test_readme_exists_and_is_authored():
    text = _read(README)
    # Authored, not a stub: substantial prose with a real section structure.
    assert len(text) >= 2000, "README looks like a stub (too small)"
    assert text.lstrip().startswith("# pengyos-appimage")
    assert text.count("\n## ") >= 5, "README lacks a real section structure"
    # All three subcommands are documented.
    for cmd in ("inspect", "integrate", "check"):
        assert cmd in text, f"README usage docs do not mention {cmd!r}"
    # The security invariant must be documented.
    assert "never runs or unpacks" in text.lower()
    # Install instructions are present.
    assert "pip install" in text
    # The documented flags are actually wired (see test_smoke.py).
    for flag in ("--remove", "--libdirs", "--json"):
        assert flag in text, f"README usage docs do not mention {flag!r}"


def test_readme_documents_the_license():
    text = _read(README)
    assert "MIT" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_readme_documents_every_package_module():
    """Docs stay in sync: every module in the package is in the layout tree."""
    text = _read(README)
    for module in sorted(PACKAGE_DIR.glob("*.py")):
        assert module.name in text, (
            f"{module.name} exists in src/pengyos_appimage/ but is not "
            "documented in README.md (project layout)"
        )


def test_readme_documents_every_test_file():
    """Docs stay in sync: every test file is in the layout tree."""
    text = _read(README)
    for test_file in sorted(TESTS_DIR.glob("test_*.py")):
        assert test_file.name in text, (
            f"{test_file.name} exists in tests/ but is not documented in "
            "README.md (project layout)"
        )


# --- LICENSE -----------------------------------------------------------------


def test_license_is_mit_with_required_copyright():
    text = _read(LICENSE)
    assert text.lstrip().startswith("MIT License")
    assert "Copyright (c) 2026 Pat Wendorf" in text
    # Core MIT clauses (wording checked so a silent re-license is caught).
    assert "Permission is hereby granted, free of charge" in text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in text


def test_pyproject_declares_the_mit_license():
    text = _read(PYPROJECT)
    assert 'license = { text = "MIT" }' in text
    assert "License :: OSI Approved :: MIT License" in text


# --- CHANGELOG.md ------------------------------------------------------------


def test_changelog_is_keep_a_changelog():
    text = _read(CHANGELOG)
    assert "Keep a Changelog" in text
    assert "Semantic Versioning" in text
    # Version headings must look like ``## [X.Y.Z] - date``.
    assert _version_headings(text), "no '## [version] - date' headings found"


def test_changelog_current_version_matches_package():
    """The topmost changelog entry must be the package's current version, so a
    release without a changelog entry fails the suite (docs stay in sync)."""
    text = _read(CHANGELOG)
    versions = _version_headings(text)
    assert versions[0] == __version__, (
        f"CHANGELOG.md top entry is [{versions[0]!r}] but the package version "
        f"is {__version__!r}"
    )


# --- .gitignore --------------------------------------------------------------


def test_gitignore_covers_build_test_and_runtime_noise():
    text = _read(PYTHONIGNORE)
    assert text.strip(), ".gitignore is empty"
    # Python build/test noise.
    assert "__pycache__" in text
    assert "*.py[cod]" in text or "*.pyc" in text
    assert ".pytest_cache" in text
    assert "build/" in text
    assert "dist/" in text
    # Virtual environments.
    assert ".venv" in text or "venv/" in text
    # Real AppImages used for opt-in tests must never be committed.
    assert "*.AppImage" in text


def test_gitignored_artifacts_are_not_committed():
    """A gitignored file should not also be tracked by git — if it is, the
    tree is not clean and something leaked into the source tree. The tool
    lives under ``tools/pengyos-appimage`` of the PengyOS monorepo, so
    ``.git`` can be an ancestor, not the repo root."""
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
        if name.endswith((".pyc", ".pyo"))
        or "__pycache__" in name
        or name.endswith((".egg-info",))
        or ".pytest_cache" in name
        or name.endswith(".AppImage")
    ]
    assert not offenders, f"gitignored artifacts are tracked: {offenders}"


# --- full green suite (opt-in integration gate) ------------------------------


@pytest.mark.skipif(
    os.environ.get("PENGYOS_IT") != "1",
    reason="opt-in integration gate: run with PENGYOS_IT=1",
)
def test_full_suite_green_opt_in():
    """Re-runs the whole suite in a subprocess and asserts it exits 0 — the
    spec's 'full green test suite' clause, made assertable. ``PENGYOS_IT`` is
    removed from the child environment so this cannot recurse."""
    env = {k: v for k, v in os.environ.items() if k != "PENGYOS_IT"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = (proc.stdout or "")[-2000:] + "\n" + (proc.stderr or "")[-2000:]
    assert proc.returncode == 0, f"inner suite failed (rc={proc.returncode}):\n{tail}"
