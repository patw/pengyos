"""GitHub-ready contract checks (spec.md).

Enforced by ``test_github_ready``. The scaffold covers the required files, a
``pyproject.toml`` declaring the console script, an importable package, and a
non-empty test suite. This suite also verifies the docs are *in sync* with the
actual code (README documents the real CLI, the CHANGELOG is populated, and the
package version is recorded), so the contract is a real gate, not a file-exists
smoke test.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent

_SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")


def _exists(name: str) -> pathlib.Path:
    path = ROOT / name
    assert path.exists(), f"missing required file: {name}"
    assert path.stat().st_size > 0, f"required file is empty: {name}"
    return path


def test_required_docs_present():
    _exists("README.md")
    _exists("CHANGELOG.md")
    _exists(".gitignore")


def test_license_is_mit_with_copyright():
    text = _exists("LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_pyproject_declares_console_script():
    with _exists("pyproject.toml").open("rb") as fh:
        pyproject = tomllib.load(fh)
    scripts = pyproject["project"]["scripts"]
    assert "pengyos-repro-report" in scripts
    assert scripts["pengyos-repro-report"] == "pengyos_repro_report.cli:main"


def test_package_importable():
    import pengyos_repro_report  # noqa: F401

    import pengyos_repro_report.core  # noqa: F401
    import pengyos_repro_report.cli  # noqa: F401


def test_non_empty_test_suite():
    tests_dir = ROOT / "tests"
    test_files = sorted(tests_dir.glob("test_*.py"))
    assert test_files, "no test files found"
    total_assertions = 0
    for test_file in test_files:
        text = test_file.read_text(encoding="utf-8")
        total_assertions += text.count("assert ")
    assert total_assertions > 0


def _cli_subcommands() -> list[str]:
    """The subcommands the CLI actually exposes (discovers, does not hard-code).

    ``argparse`` stores the registered subparsers on ``parser._actions`` as a
    single ``_SubParsersAction`` whose ``choices`` are the subcommand names.
    That private accessor has been stable since Python 3.2, unlike the public
    ``get_actions`` (present 3.11-3.13, gone in 3.14) and the return value of
    ``add_subparsers`` (an action in 3.14, the subparser parser before). We
    resolve the registered names via ``_actions`` so the gate works everywhere.
    """
    import pengyos_repro_report.cli as cli

    parser = cli.build_parser()
    action = next(
        (a for a in parser._actions if type(a).__name__ == "_SubParsersAction"),
        None,
    )
    assert action is not None, "CLI registers no subcommands"
    assert action.choices, "CLI registers no subcommands"
    return sorted(action.choices)


def _readme() -> str:
    return _exists("README.md").read_text(encoding="utf-8")


def test_readme_documents_the_actual_cli_interface():
    """The README must document every subcommand the CLI really exposes."""
    readme = _readme()
    assert "pengyos-repro-report" in readme, "README does not mention the console script"
    subcommands = _cli_subcommands()
    assert subcommands, "CLI exposes no subcommands"
    # Each subcommand must appear in a usage line, i.e. preceded by the
    # console-script name — proves the docs mirror the real interface rather
    # than merely containing the word.
    for sub in subcommands:
        assert (
            f"pengyos-repro-report {sub}" in readme
        ), f"README does not document the '{sub}' subcommand in usage"


def test_readme_has_copyright_line():
    assert "Copyright (c) 2026 Pat Wendorf" in _readme()


def test_changelog_has_a_versioned_or_unreleased_section():
    text = _exists("CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(r"^## \[(Unreleased|\d+\.\d+\.\d+)", text, re.MULTILINE), (
        "CHANGELOG has no top-level versioned or Unreleased section"
    )


def test_package_version_is_semver_and_recorded():
    with _exists("pyproject.toml").open("rb") as fh:
        pyproject = tomllib.load(fh)
    version = pyproject["project"]["version"]
    assert _SEMVER.match(version), f"pyproject version is not semver: {version!r}"
    changelog = _exists("CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog, (
        f"CHANGELOG does not record the package version {version}"
    )
