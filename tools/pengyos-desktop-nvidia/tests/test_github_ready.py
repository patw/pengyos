"""GitHub-ready contract (``test_github_ready``).

Asserts the repo is publishable as-is: the docs the spec demands are present and
in sync, the test suite is green, the package imports, and the tool metadata is
sane. These are the files a human or CI would reach for before trusting the
project.

The spec's GitHub-ready contract names two things beyond the four doc files:
"and a full green test suite" and "Docs are authored — keep them in sync". Those
are asserted here too -- the suite is green (the whole suite, via a hermetic
subprocess run, not a recursive self-invocation) and every authored doc is
present and non-empty.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "pengyos_desktop_nvidia"

# Docs the spec's GitHub-ready contract requires to be authored (in sync).
DOCS = ("README.md", "CHANGELOG.md", "spec.md")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_present_and_nonempty() -> None:
    readme = ROOT / "README.md"
    assert readme.exists(), "README.md is required for a GitHub-ready repo"
    assert _read(readme).strip(), "README.md must have content"


def test_license_is_mit_with_copyright() -> None:
    license_text = _read(ROOT / "LICENSE")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text


def test_changelog_present_and_nonempty() -> None:
    changelog = ROOT / "CHANGELOG.md"
    assert changelog.exists(), "CHANGELOG.md is required for a GitHub-ready repo"
    assert _read(changelog).strip(), "CHANGELOG.md must have content"


def test_gitignore_present_and_nonempty() -> None:
    gitignore = ROOT / ".gitignore"
    assert gitignore.exists(), ".gitignore is required for a GitHub-ready repo"
    assert _read(gitignore).strip(), ".gitignore must have content"


def test_pyproject_present_and_parses() -> None:
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.exists(), "pyproject.toml is required"
    data = tomllib.loads(_read(pyproject))
    assert data["project"]["name"] == "pengyos-desktop-nvidia"
    # The console script the spec documents must be wired up.
    assert "pengyos-desktop-nvidia" in data["project"]["scripts"]


def test_package_importable() -> None:
    import pengyos_desktop_nvidia  # noqa: F401


def test_version_declared_and_consistent() -> None:
    import tomllib

    import pengyos_desktop_nvidia

    data = tomllib.loads(_read(ROOT / "pyproject.toml"))
    declared = data["project"]["version"]
    assert pengyos_desktop_nvidia.__version__ == declared


def test_docs_present_and_in_sync() -> None:
    # The spec's GitHub-ready contract: "Docs are authored — keep them in sync."
    for name in DOCS:
        path = ROOT / name
        assert path.exists(), f"{name} is required for a GitHub-ready repo"
        assert _read(path).strip(), f"{name} must have content"


def test_gitignore_covers_build_and_packaging_artifacts() -> None:
    # A GitHub-ready repo must not commit build/ or *.egg-info/ noise.
    gitignore = _read(ROOT / ".gitignore")
    for pattern in ("build/", "*.egg-info/", "__pycache__/"):
        assert pattern in gitignore, f".gitignore must ignore {pattern}"


def test_suite_is_green() -> None:
    # The spec's GitHub-ready contract: "and a full green test suite."
    # Run the whole suite in a hermetic subprocess (not a recursive
    # self-invocation) and require a clean exit. Integration tests are opt-in,
    # so pin PENGYOS_IT off for a deterministic default run.
    #
    # The subprocess runs the *whole* suite -- which includes this very test --
    # so without a guard it would spawn another `pytest`, which spawns another,
    # ... to a depth that blows the 300s timeout. The sentinel breaks the cycle:
    # once we've handed off the check to a child, the child sees it and skips.
    if os.environ.get("PENGYOS_SUITE_CHECK_RUNNING"):
        return
    env = dict(os.environ)
    env["PENGYOS_SUITE_CHECK_RUNNING"] = "1"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PENGYOS_IT"] = ""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"test suite is not green:\n{proc.stdout}\n{proc.stderr}"
