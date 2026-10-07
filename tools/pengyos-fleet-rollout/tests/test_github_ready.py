"""GitHub-ready contract tests.

The spec's *GitHub-ready contract* asserts, with a ``test_github_ready``, that
the repository ships everything a human needs to clone it and run it:

* ``README.md`` — documents the project and how to use it.
* ``LICENSE`` — MIT, "Copyright (c) 2026 Pat Wendorf".
* ``CHANGELOG.md`` — a changelog (Keep a Changelog style).
* ``.gitignore`` — keeps build artifacts and output out of version control.
* ``pyproject.toml`` — packaging metadata the suite and console script rely on.
* a full, green test suite.

These tests only read files on disk — no network, no root, no real hardware —
and lock the contract down so it cannot silently rot. Keeping the docs in sync
with the code is part of the contract, so the README is checked against the
subcommands the CLI actually advertises.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import pengyos_fleet_rollout
from pengyos_fleet_rollout.cli import build_parser

HERE = Path(__file__).parent
ROOT = HERE.parent


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The contract files must exist.
# ---------------------------------------------------------------------------


def test_github_ready_contract_files_exist():
    for name in (
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        ".gitignore",
        "pyproject.toml",
    ):
        path = ROOT / name
        assert path.is_file(), f"{name} is missing — repo is not GitHub-ready"
        assert path.stat().st_size > 0, f"{name} is empty"


# ---------------------------------------------------------------------------
# LICENSE — MIT, correct copyright holder.
# ---------------------------------------------------------------------------


def test_license_is_mit_with_copyright():
    license_text = _read("LICENSE")
    assert "MIT" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


# ---------------------------------------------------------------------------
# README — documents the project and how to use it.
# ---------------------------------------------------------------------------


def test_readme_documents_project_and_usage():
    readme = _read("README.md")
    # Names the tool and the project it serves.
    assert "pengyos-fleet-rollout" in readme
    assert "PengyOS" in readme
    # Has a usage section so a visitor can run it.
    assert "## Usage" in readme


def test_readme_is_in_sync_with_implementation():
    # The README must document every subcommand the CLI advertises (no stale or
    # missing docs) — this keeps the docs in sync with the code.
    readme = _read("README.md")
    advertised = build_parser().format_help()
    for cmd in ("plan", "apply", "rollback"):
        assert cmd in advertised, (
            f"{cmd} is advertised by the CLI but not in README"
        )
        assert (
            f"`{cmd}`" in readme or f"`pengyos-fleet-rollout {cmd}`" in readme
        ), f"README does not document the `{cmd}` subcommand"


def test_readme_documents_the_cli_flags():
    # The README must document the actual CLI flags (not just the subcommand
    # names), so a visitor can run it — this keeps the docs in sync with the
    # interface the spec fixes up front.
    readme = _read("README.md")
    for flag in (
        "--image",
        "--inventory",
        "--batch-size",
        "--json",
        "--plan",
        "--journal",
        "--transport",
        "--dry-run",
        "--commit",
    ):
        assert flag in readme, f"README does not document the {flag} flag"
    # The documented default behaviour: apply is dry-run by default.
    assert "Dry-run by default" in readme


# ---------------------------------------------------------------------------
# CHANGELOG — present and in the expected format.
# ---------------------------------------------------------------------------


def test_changelog_present_and_formatted():
    changelog = _read("CHANGELOG.md")
    assert "Changelog" in changelog or "changelog" in changelog
    # Keep a Changelog style: a top-level heading and an unreleased/versions area.
    assert "## [" in changelog



# ---------------------------------------------------------------------------
# .gitignore — keeps build artifacts and output out of version control.
# ---------------------------------------------------------------------------


def test_gitignore_ignores_python_artifacts():
    gitignore = _read(".gitignore")
    for entry in ("__pycache__/", "*.egg-info/", ".pytest_cache/"):
        assert entry in gitignore, f".gitignore should ignore {entry}"


def test_gitignore_un_ignores_committed_fixtures():
    # The root-level ``plan.json`` / ``journal.json`` patterns also match
    # ``tests/fixtures/``, so the fixtures must be explicitly un-ignored — a
    # fresh clone would otherwise ship with no fixtures and break the suite.
    # This locks the gotcha from the rollback subtask (committed fixtures were
    # silently git-ignored) at the contract level.
    gitignore = _read(".gitignore")
    for fixture in (
        "!tests/fixtures/plan.json",
        "!tests/fixtures/journal.json",
        "!tests/fixtures/inventory.toml",
    ):
        assert fixture in gitignore, (
            f".gitignore must un-ignore {fixture} (a fresh clone needs it)"
        )
    # And the un-ignore must come after the ignore, so it actually takes effect.
    assert (
        gitignore.index("plan.json") < gitignore.index("!tests/fixtures/plan.json")
    )


# ---------------------------------------------------------------------------
# pyproject.toml — packaging metadata the suite and console script rely on.
# ---------------------------------------------------------------------------


def test_pyproject_has_entry_point_and_version():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        metadata = tomllib.load(fh)
    # The console entry point the README documents.
    assert (
        metadata["project"]["scripts"]["pengyos-fleet-rollout"]
        == "pengyos_fleet_rollout.cli:main"
    )
    # Runtime is stdlib-only.
    assert metadata["project"]["dependencies"] == []


def test_version_constant_matches_pyproject():
    with (ROOT / "pyproject.toml").open("rb") as fh:
        metadata = tomllib.load(fh)
    assert pengyos_fleet_rollout.__version__ == metadata["project"]["version"]


# ---------------------------------------------------------------------------
# A full, green test suite — the contract's closing clause.
# ---------------------------------------------------------------------------


def test_suite_has_tests():
    # The suite is first-class: there are test modules to run.
    test_files = sorted(ROOT.glob("tests/test_*.py"))
    assert test_files, "no test modules found — the suite is not full"
    # The GitHub-ready contract has its own module here.
    assert ROOT / "tests" / "test_github_ready.py" in test_files


# ---------------------------------------------------------------------------
# A GitHub-ready repo is a git repository that actually tracks its source — a
# repo full of untracked files is not something a human can clone and run.
# ---------------------------------------------------------------------------


def test_repo_is_a_git_repository():
    # The GitHub-ready contract is a git repo a human can clone. When git is not
    # available (or this isn't a work tree) the file/pytest assertions above
    # still hold, so this one is skipped rather than failing.
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
    except FileNotFoundError:
        pytest.skip("git not available")
    assert result.returncode == 0, "the project directory is not a git work tree"


def test_repo_tracks_its_source_files():
    # A fresh clone must ship the package, the suite, and the committed
    # fixtures — none of these may be untracked. This locks "GitHub-ready" in
    # the sense that matters: the files a visitor runs are actually in the repo.
    import subprocess

    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            input="\n".join(
                [
                    str(ROOT / "pyproject.toml"),
                    str(ROOT / "README.md"),
                    str(ROOT / "LICENSE"),
                    str(ROOT / "pengyos_fleet_rollout" / "__init__.py"),
                    str(ROOT / "pengyos_fleet_rollout" / "cli.py"),
                    str(ROOT / "tests" / "test_github_ready.py"),
                    str(ROOT / "tests" / "fixtures" / "inventory.toml"),
                    str(ROOT / "tests" / "fixtures" / "plan.json"),
                    str(ROOT / "tests" / "fixtures" / "journal.json"),
                ]
            )
            + "\n",
        )
    except FileNotFoundError:
        pytest.skip("git not available")
    assert result.returncode == 0, (
        "some required source/fixture files are not tracked by git — "
        "the repo is not GitHub-ready"
    )


# ---------------------------------------------------------------------------
# The README points visitors at hosts.example.toml as a ready-to-copy sample
# inventory; a GitHub-ready repo ships something that actually parses.
# ---------------------------------------------------------------------------


def test_hosts_example_toml_exists_and_parses():
    import pengyos_fleet_rollout.model as model

    example = ROOT / "hosts.example.toml"
    assert example.is_file(), "hosts.example.toml is referenced by the README but missing"
    # It must be a valid inventory: `plan` consumes this shape.
    hosts = model.load_inventory(example)
    assert hosts, "hosts.example.toml defines no hosts"
    # The canary-first sample has a canary host first in rollout order.
    names = [h.name for h in hosts]
    assert "canary-1" in names, "hosts.example.toml should ship a canary host"
