"""The GitHub-ready contract (spec: Verification).

Asserts the repo carries the metadata every GitHub-ready project needs, and that
the test suite itself is present and importable. This is a first-class
requirement, not a nicety.

Beyond "the files exist", these tests pin the *content* of that metadata so the
contract is genuinely verified:

- the LICENSE is a complete MIT license with the correct copyright holder,
- the README documents every CLI command (spec: "Docs are authored — keep them
  in sync" — the docs must not drift from the code),
- the CHANGELOG has a current (Unreleased / versioned) section,
- the console scripts are declared in ``pyproject.toml`` so ``pip install`` yields
  the entry points.
"""

import importlib
import importlib.util
import os
import pathlib
import re
import sys
import tomllib

import pytest

from pengyos_install import __version__, cli

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
    assert "pengyos-install" in pyproject


def test_pyproject_defines_console_scripts():
    """The console entry points must be declared so `pip install` yields the CLIs."""
    pyproject = _read("pyproject.toml")
    assert "[project.scripts]" in pyproject
    assert 'pengyos-install = "pengyos_install.cli:main"' in pyproject
    # Generic names (`install` especially) would shadow coreutils on PATH.
    scripts = pyproject.split("[project.scripts]", 1)[1].split("[", 1)[0]
    for name in ("plan", "install", "provision-plan"):
        assert f"\n{name} =" not in scripts, f"console script {name!r} shadows a system tool"


def test_test_suite_is_present():
    tests_dir = REPO_ROOT / "tests"
    assert tests_dir.is_dir(), "tests/ directory is missing"
    test_files = sorted(tests_dir.glob("test_*.py"))
    assert test_files, "no test_*.py files found under tests/"


def test_package_is_importable():
    # The package must be importable from the tree (see conftest.py path setup).
    spec = importlib.util.find_spec("pengyos_install")
    assert spec is not None, "pengyos_install package is not importable"
    assert spec.origin is not None
    assert os.path.basename(os.path.dirname(spec.origin)) == "pengyos_install"


def test_package_exposes_version():
    assert isinstance(__version__, str)
    assert __version__, "package version must be a non-empty string"


@pytest.mark.parametrize(
    "name",
    ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"],
)
def test_expected_artifacts_exist(name):
    assert (REPO_ROOT / name).is_file(), f"{name} should exist in the repo root"


def test_readme_documents_the_three_console_scripts():
    """Every console script name must be documented in the README."""
    readme = _read("README.md").lower()
    for script in ("pengyos-install", "plan", "install", "provision-plan"):
        assert script in readme, f"README does not document the {script!r} script"


# --- The GitHub-ready contract is more than "the files exist" -----------------
#
# The tests above pin that the metadata is present. The tests below pin that the
# metadata is *correct* and internally consistent -- the difference between a
# repo that merely looks GitHub-ready and one that actually installs, documents,
# and version-locks cleanly. These are stdlib-only, no network / root / hardware.

_PYPROJECT = REPO_ROOT / "pyproject.toml"


def _pyproject() -> dict:
    # tomllib ships with Python 3.11+. On 3.10 the package backfills tomli, but
    # the test suite itself only needs it here; skip cleanly rather than hard
    # fail on an older interpreter.
    if sys.version_info < (3, 11):  # pragma: no cover - CI is 3.11+
        pytest.skip("pyproject parsing needs tomllib (Python 3.11+)")
    return tomllib.loads(_read("pyproject.toml"))


def test_console_scripts_resolve_to_callables():
    """Every declared console script must import to a *callable* entry point.

    The name-only check above proves the strings are present; this proves the
    contract the spec makes real: ``pip install`` yields working CLIs. Each
    ``name = "module:func"`` is imported and confirmed callable.
    """
    scripts = _pyproject()["project"]["scripts"]
    assert scripts, "no console scripts declared"
    for name, target in scripts.items():
        module_name, _, attr = target.partition(":")
        assert _, f"console script {name!r} target {target!r} is not module:func"
        module = importlib.import_module(module_name)
        func = getattr(module, attr, None)
        assert func is not None, f"{name!r} -> {module_name}.{attr} does not exist"
        assert callable(func), f"{name!r} -> {module_name}.{attr} is not callable"


def test_version_matches_pyproject():
    """The package ``__version__`` must equal ``[project] version`` in pyproject."""
    version = _pyproject()["project"]["version"]
    assert __version__ == version, (
        f"version drift: __version__={__version__!r} != pyproject {version!r}"
    )
    # And it must be a real, non-trivial version.
    assert re.match(r"^\d+\.\d+\.\d+", version), f"{version!r} is not semver"


def test_readme_documents_every_cli_flag():
    """The README must document every flag the CLI actually exposes.

    Documenting only the subcommands is not enough -- the flags in the usage
    lines are part of the documented interface and must be in sync.
    """
    readme = _read("README.md").lower()
    flags = _all_cli_flags(cli.build_parser())
    undocumented = sorted(f for f in flags if f not in readme)
    assert not undocumented, (
        f"README does not document these CLI flags: {undocumented}"
    )


def _all_cli_flags(parser) -> set[str]:
    """Every long-flag name the parser (and its subparsers) exposes."""
    flags: set[str] = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):  # a subparsers action
            for sub in choices.values():
                for sub_action in sub._actions:
                    for opt in sub_action.option_strings:
                        flags.add(opt.lstrip("-"))
    return flags
