"""The GitHub-ready contract (spec: Verification).

Asserts the repo carries the metadata every GitHub-ready project needs, and
that the test suite itself is present and importable. This is a first-class
requirement, not a nicety.

Beyond "the files exist", these tests pin the *content* of that metadata so the
contract is genuinely verified:

- the LICENSE is a complete MIT license with the correct copyright holder,
- the README documents every CLI command (spec: "Docs are authored — keep them
  in sync" — the docs must not drift from the code),
- the CHANGELOG has a current (Unreleased / versioned) section,
- the console script is declared in ``pyproject.toml`` so ``pip install`` yields
  the ``pengyos-guest`` entry point.
"""

import importlib.util
import os
import pathlib

import pytest

from pengyos_guest import __version__, cli

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    path = REPO_ROOT / name
    assert path.is_file(), f"{name} is missing from the repo root"
    return path.read_text(encoding="utf-8")


def _subcommand_names(parser) -> set[str]:
    """The subcommands *parser* exposes (version-agnostic discovery).

    ``argparse``'s subparsers action class is NOT public in 3.14, so the
    choices are found by scanning actions for a dict-valued ``choices``.
    """
    return {
        choice
        for action in parser._actions
        if isinstance(getattr(action, "choices", None), dict)
        for choice in action.choices
    }


def test_readme_present_and_nonempty():
    readme = _read("README.md")
    assert "pengyos" in readme.lower() or "PengyOS" in readme


def test_readme_documents_every_cli_command():
    """Docs are kept in sync: every CLI subcommand is documented in the README."""
    readme = _read("README.md")
    documented = {c.lower() for c in _subcommand_names(cli.build_parser())}
    lowered = readme.lower()
    for command in documented:
        assert command in lowered, f"README does not document the {command!r} command"


def test_license_is_mit_with_copyright():
    license_text = _read("LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


def test_license_is_full_mit_text():
    """A GitHub-ready LICENSE is a complete MIT license, not a fragment."""
    license_text = _read("LICENSE")
    # Standard MIT clauses — the canonical text is what makes it a valid MIT file.
    for clause in (
        "Permission is hereby granted, free of charge",
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY',
        "OR IN CONNECTION WITH THE SOFTWARE",
    ):
        assert clause in license_text, f"LICENSE missing standard MIT clause: {clause!r}"


def test_changelog_present_and_nonempty():
    changelog = _read("CHANGELOG.md")
    assert len(changelog.strip()) > 0


def test_changelog_has_a_current_version_section():
    """A GitHub-ready changelog has a current (Unreleased or versioned) section."""
    changelog = _read("CHANGELOG.md")
    assert "## [" in changelog, "CHANGELOG has no versioned/Unreleased section"


def test_gitignore_present_and_nonempty():
    gitignore = _read(".gitignore")
    assert len(gitignore.strip()) > 0


def test_pyproject_present_and_nonempty():
    pyproject = _read("pyproject.toml")
    assert "pengyos-guest" in pyproject


def test_pyproject_defines_console_script():
    """The console entry point must be declared so `pip install` yields the CLI."""
    pyproject = _read("pyproject.toml")
    assert "[project.scripts]" in pyproject
    assert "pengyos-guest" in pyproject
    assert "pengyos_guest.cli:main" in pyproject


def test_test_suite_is_present():
    tests_dir = REPO_ROOT / "tests"
    assert tests_dir.is_dir(), "tests/ directory is missing"
    test_files = sorted(tests_dir.glob("test_*.py"))
    assert test_files, "no test_*.py files found under tests/"


def test_package_is_importable():
    # The package must be importable from the tree (see conftest.py path setup).
    spec = importlib.util.find_spec("pengyos_guest")
    assert spec is not None, "pengyos_guest package is not importable"
    assert spec.origin is not None
    assert os.path.basename(os.path.dirname(spec.origin)) == "pengyos_guest"


def test_package_exposes_version():
    assert isinstance(__version__, str)
    assert __version__, "package version must be a non-empty string"


@pytest.mark.parametrize(
    "name",
    ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"],
)
def test_expected_artifacts_exist(name):
    assert (REPO_ROOT / name).is_file(), f"{name} should exist in the repo root"
