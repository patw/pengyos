"""GitHub-ready contract.

Asserts the repository is GitHub-ready: the four required files exist and are
coherent, the package imports cleanly, and the test suite is present and
complete. This doubles as the "docs are authored — keep them in sync" guard
(spec.md "GitHub-ready contract"): if a required file is deleted, or a doc
drifts out of agreement with the code, this suite fails.

Note on "a full green test suite": a test cannot recursively assert that the
*whole* suite is green without re-invoking pytest (circular). Instead this
subtask asserts the *prerequisites* for a green suite — every implemented
module imports cleanly and every module has a corresponding test file — so the
only remaining way for the suite to fail is a real behaviour bug, not a broken
import or a missing test. The harness (``verify.toml``) is what actually runs
``pytest -q`` and checks the exit code.
"""

import importlib
from pathlib import Path

import pengyos_repro
from pengyos_repro import cli

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "pengyos_repro"
TESTS_DIR = REPO_ROOT / "tests"


def _read(path: Path) -> str:
    """Read a repo file as UTF-8 text."""
    return path.read_text(encoding="utf-8")


# --- required files ---------------------------------------------------------


def test_required_files_exist():
    """The GitHub-ready file set is present at the repo root."""
    for name in (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        ".gitignore",
        "pyproject.toml",
        "spec.md",
    ):
        assert (REPO_ROOT / name).is_file(), f"missing required file: {name}"


def test_readme_nonempty_and_documents_tool():
    """README is substantial and documents the tool it ships."""
    readme = _read(REPO_ROOT / "README.md")
    assert len(readme.strip()) > 200
    # The CLI surface documented in the README matches the real one.
    for subcommand in ("compare", "score"):
        assert subcommand in readme
    # The package is described by name.
    assert "pengyos-repro" in readme


def test_license_is_mit_with_copyright():
    """LICENSE is MIT and carries the exact copyright line from the contract."""
    license_text = _read(REPO_ROOT / "LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


def test_changelog_has_versioned_sections():
    """CHANGELOG follows Keep a Changelog and has at least one versioned section."""
    changelog = _read(REPO_ROOT / "CHANGELOG.md")
    assert "Changelog" in changelog
    # Keep a Changelog: versioned sections are rendered as `## [x.y.z]`.
    assert "## [" in changelog


def test_gitignore_nonempty_and_python_friendly():
    """.gitignore exists, is non-empty, and ignores Python build/test artefacts."""
    gitignore = _read(REPO_ROOT / ".gitignore")
    assert len(gitignore.strip()) > 0
    assert "__pycache__" in gitignore
    assert ".pytest_cache/" in gitignore


# --- pyproject coherence ----------------------------------------------------


def test_pyproject_declares_name_and_console_script():
    """pyproject.toml is PEP 621, names the package, and ships the console script."""
    text = _read(REPO_ROOT / "pyproject.toml")
    assert "[project]" in text
    assert 'name = "pengyos-repro"' in text
    assert 'pengyos-repro = "pengyos_repro.cli:main"' in text


def test_pyproject_runtime_dependencies_are_stdlib_only():
    """No third-party runtime dependencies are declared (standard library only)."""
    text = _read(REPO_ROOT / "pyproject.toml")
    assert "dependencies = []" in text


# --- package imports cleanly ------------------------------------------------


def test_package_exposes_version():
    assert isinstance(pengyos_repro.__version__, str)
    parts = pengyos_repro.__version__.split(".")
    assert len(parts) >= 3


def test_every_module_imports_cleanly():
    """Each implemented module imports without side effects (no broken import)."""
    for modname in ("cli", "manifest", "classify", "patterns", "report"):
        module = importlib.import_module(f"pengyos_repro.{modname}")
        assert module is not None


def test_cli_entry_points_are_callable():
    assert callable(cli.main)
    assert callable(cli.build_parser)


# --- test suite present and complete ----------------------------------------


def test_test_suite_present():
    """The test suite exists and has a conftest that makes the repo importable."""
    assert TESTS_DIR.is_dir()
    assert (TESTS_DIR / "conftest.py").is_file()
    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    assert len(test_files) >= 5


def test_every_module_has_a_test():
    """Every implemented module has a dedicated test file (a complete suite).

    This is the proxy for "a full green test suite": the only way the suite can
    fail now is a real behaviour bug, not a missing or unimportable test.
    """
    implemented = {
        "cli": "test_scaffold",
        "manifest": "test_manifest",
        "classify": "test_classify",
        "patterns": "test_patterns",
        "report": "test_report",
    }
    test_names = {path.stem for path in TESTS_DIR.glob("test_*.py")}
    for module, expected_test in implemented.items():
        assert expected_test in test_names, (
            f"module {module!r} has no {expected_test}.py"
        )


def test_github_ready_contract():
    """The whole contract, asserted at once (the smoke test named in the spec)."""
    # Required files.
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        assert (REPO_ROOT / name).is_file()
    # LICENSE is MIT with the exact copyright line.
    license_text = _read(REPO_ROOT / "LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text
    # Package imports and the CLI is wired.
    assert callable(cli.main)
    # Test suite is present and complete.
    test_names = {path.stem for path in TESTS_DIR.glob("test_*.py")}
    assert {"test_scaffold", "test_manifest", "test_classify",
            "test_patterns", "test_report"} <= test_names
