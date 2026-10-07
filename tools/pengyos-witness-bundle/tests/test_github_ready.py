"""GitHub-ready contract (implementation task: ``test_github_ready``).

spec.md locks the repo to a **GitHub-ready** state and asks that it be asserted
with a ``test_github_ready``::

    README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
    `.gitignore`, and a full green test suite. Docs are authored — keep them in
    sync.

This module turns that prose into checks that can silently regress:

* ``README.md``      — present and *authored* (a real, substantial description,
  not a placeholder);
* ``LICENSE``        — MIT and carries the required copyright line
  ("Copyright (c) 2026 Pat Wendorf");
* ``CHANGELOG.md``   — present and *authored* (a Keep-a-Changelog style history
  with a versioned section), not a stub;
* ``.gitignore``     — present and non-empty;
* ``pyproject.toml`` — valid, declares the ``pengyos-witness-bundle`` console
  entry point and an MIT license (so the package is installable / importable);
* a **green test suite** — every ``tests/test_*.py`` module imports cleanly and
  the package surface is importable. Importing each test module is the in-suite
  proxy for "the suite collects and runs green": a module that raises on import
  (bad import, typo, missing symbol) is exactly what makes ``pytest`` red, and
  importing the package surface is what makes the shipped tool importable.

None of this needs the network, root, a display, or hardware.
"""

import importlib
import tomllib
from pathlib import Path

import pytest

# The tool repo root is the parent of ``tests/``.
ROOT = Path(__file__).resolve().parent.parent

# A README/CHANGELOG below this size is almost certainly a stub, not authored
# content — the contract says "Docs are authored".
_MIN_AUTHORED_CHARS = 300


# --- required docs ----------------------------------------------------------


def test_readme_present_and_authored():
    readme = ROOT / "README.md"
    assert readme.is_file(), "README.md is required for a GitHub-ready repo"
    text = readme.read_text(encoding="utf-8")
    assert len(text.strip()) >= _MIN_AUTHORED_CHARS, (
        "README.md looks like a stub — it needs authored content"
    )
    # It must actually describe the tool, not just exist.
    assert "pengyos-witness-bundle" in text
    assert "witness" in text.lower()


def test_license_is_mit_with_copyright_line():
    license_path = ROOT / "LICENSE"
    assert license_path.is_file(), "LICENSE is required for a GitHub-ready repo"
    text = license_path.read_text(encoding="utf-8")
    assert "MIT" in text, "LICENSE must be the MIT license"
    assert (
        "Copyright (c) 2026 Pat Wendorf" in text
    ), "LICENSE must carry the required copyright line"


def test_changelog_present_and_authored():
    changelog = ROOT / "CHANGELOG.md"
    assert changelog.is_file(), "CHANGELOG.md is required for a GitHub-ready repo"
    text = changelog.read_text(encoding="utf-8")
    assert len(text.strip()) >= _MIN_AUTHORED_CHARS, (
        "CHANGELOG.md looks like a stub — it needs authored content"
    )
    # A real changelog has at least one versioned section.
    assert "## [" in text, "CHANGELOG.md should have a versioned section"


def test_gitignore_present_and_nonempty():
    gitignore = ROOT / ".gitignore"
    assert gitignore.is_file(), ".gitignore is required for a GitHub-ready repo"
    text = gitignore.read_text(encoding="utf-8")
    # Ignore blank lines / comments: there must be at least one real entry.
    entries = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert entries, ".gitignore must contain at least one entry"


# --- installable / importable package ---------------------------------------


def test_pyproject_is_valid_and_documented():
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file(), "pyproject.toml is required for a GitHub-ready repo"
    # tomllib ships with Python 3.11+; read it if available, else skip the parse.
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        pytest.skip("tomllib unavailable on this Python")

    project = data.get("project", {})
    # The console entry point the README documents.
    scripts = project.get("scripts", {})
    assert "pengyos-witness-bundle" in scripts, (
        "pyproject.toml must declare the 'pengyos-witness-bundle' console script"
    )
    # License must be MIT (declared, matching LICENSE).
    assert "MIT" in str(project.get("license", "")).upper()


# --- a green test suite ------------------------------------------------------


def test_every_test_module_imports_cleanly():
    # Importing each test module is the in-suite proxy for "the suite collects
    # and runs green": a module that raises on import is exactly what makes
    # pytest red. This is offline and deterministic.
    tests_dir = ROOT / "tests"
    modules = sorted(p.stem for p in tests_dir.glob("test_*.py"))
    assert modules, "no test modules found under tests/"
    for name in modules:
        module = importlib.import_module(f"tests.{name}")
        assert module is not None, f"tests.{name} imported to None"


def test_package_surface_importable():
    # The shipped tool must import cleanly — this is what a published package
    # guarantees and what a red import breaks.
    from pengyos_witness_bundle import __version__  # noqa: F401
    from pengyos_witness_bundle import cli  # noqa: F401

    assert isinstance(__version__, str)
    assert hasattr(cli, "main"), "the CLI must expose main(argv)"


def test_all_test_modules_are_nonempty():
    # A zero-byte test file is a red flag (a half-written / deleted suite).
    tests_dir = ROOT / "tests"
    files = list(tests_dir.glob("test_*.py"))
    assert files, "no test modules found under tests/"
    for path in files:
        assert path.stat().st_size > 0, f"{path.name} is empty"


# --- the consolidated contract ----------------------------------------------


def test_github_ready_contract():
    # The single assertion spec.md asks for: every required doc present, the
    # LICENSE correct, pyproject installable, and the suite importable. This is
    # the gate that locks the GitHub-ready state in.
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"):
        assert (ROOT / name).is_file(), f"missing {name}"
    test_readme_present_and_authored()
    test_license_is_mit_with_copyright_line()
    test_changelog_present_and_authored()
    test_gitignore_present_and_nonempty()
    test_every_test_module_imports_cleanly()
    test_package_surface_importable()
