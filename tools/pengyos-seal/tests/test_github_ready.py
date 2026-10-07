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
import subprocess
import sys
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
    assert "pengyos-seal" in scripts
    assert scripts["pengyos-seal"] == "pengyos_seal.cli:main"


def test_package_importable():
    import pengyos_seal  # noqa: F401

    import pengyos_seal.cli  # noqa: F401
    import pengyos_seal.runner  # noqa: F401


def test_non_empty_test_suite():
    tests_dir = ROOT / "tests"
    test_files = sorted(tests_dir.glob("test_*.py"))
    assert test_files, "no test files found"
    total_assertions = 0
    for test_file in test_files:
        if test_file.name == "test_github_ready.py":
            continue
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
    import pengyos_seal.cli as cli

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
    assert "pengyos-seal" in readme, "README does not mention the console script"
    subcommands = _cli_subcommands()
    assert subcommands, "CLI exposes no subcommands"
    # Each subcommand must appear in a usage line, i.e. preceded by the
    # console-script name — proves the docs mirror the real interface rather
    # than merely containing the word.
    for sub in subcommands:
        assert (
            f"pengyos-seal {sub}" in readme
        ), f"README does not document the '{sub}' subcommand in usage"


def _all_parser_options() -> set[str]:
    """Every option string the CLI really exposes (top-level + all subparsers).

    Collected from the registered subparsers (see ``_cli_subcommands``) plus the
    top-level ``--version`` flag, so a documented ``--build`` on either subcommand
    is honoured.
    """
    import pengyos_seal.cli as cli

    parser = cli.build_parser()
    options = {"--version"}
    for action in parser._actions:
        if type(action).__name__ == "_SubParsersAction":
            for subparser in action.choices.values():
                for sub_action in subparser._actions:
                    options.update(sub_action.option_strings)
    return options


def test_readme_documents_only_real_subcommands():
    """Every `pengyos-seal <sub>` usage line maps to a real subcommand.

    The one-directional check above proves the docs cover the real interface;
    this proves the docs do not *hallucinate* one — every documented usage line
    must name a subcommand the CLI actually registers.
    """
    readme = _readme()
    subcommands = _cli_subcommands()
    for documented in re.findall(r"pengyos-seal (\w+)", readme):
        assert documented in subcommands, (
            f"README documents subcommand '{documented}' which the CLI does not expose"
        )


def test_readme_flags_are_real():
    """Every `--flag` the README documents exists on the real parser.

    The README documents concrete flags (`--pub`, `--prev`, `--json`, ...). If a
    flag is documented but not implemented (or renamed), the docs have drifted —
    this is the "docs in sync" half of the GitHub-ready contract.
    """
    readme = _readme()
    documented = {f"--{name}" for name in re.findall(r"--([a-zA-Z][\w-]*)", readme)}
    documented.discard("--version")
    assert documented, "README documents no --flags to check"
    real = _all_parser_options()
    undocumented = sorted(documented - real)
    assert not undocumented, (
        f"README documents flags the CLI does not expose: {undocumented}"
    )


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


def test_suite_is_green():
    """The rest of the suite (this file excluded) runs green."""
    tests_dir = ROOT / "tests"
    other = sorted(str(p) for p in tests_dir.glob("test_*.py") if p.name != "test_github_ready.py")
    assert other, "no other test files to run"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *other],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        f"pytest failed on the non-github-ready tests:\n{result.stdout}\n{result.stderr}"
    )
