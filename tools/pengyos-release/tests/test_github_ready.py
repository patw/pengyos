"""The GitHub-ready contract (from spec.md).

Asserts the repo ships with README, an MIT LICENSE naming the correct copyright
holder, a CHANGELOG, a .gitignore, and that the package imports cleanly with its
console entry point registered. The suite being green is the objective function;
this test guards the docs/metadata that make it shippable.
"""

from __future__ import annotations

import re
from pathlib import Path

import pengyos_release
from pengyos_release import cli
from pengyos_release.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]

# A "Copyright (c) YYYY <holder>" line, as found in LICENSE / README.
_COPYRIGHT_RE = re.compile(r"Copyright \(c\) \d{4} .+", re.MULTILINE)
# Keep a Changelog version heading, e.g. "## [0.6.0]".
_VERSION_HEADING_RE = re.compile(r"^## \[\d+\.\d+\.\d+\]", re.MULTILINE)


def _copyright_line(text: str) -> str | None:
    match = _COPYRIGHT_RE.search(text)
    if not match:
        return None
    # Normalise trailing sentence punctuation so a README sentence
    # ("...Pat Wendorf.") still agrees with the bare LICENSE line.
    return match.group(0).rstrip().rstrip(".").strip()


def test_readme_exists() -> None:
    assert (REPO_ROOT / "README.md").is_file(), "README.md is required"


def test_license_is_mit_and_names_copyright_holder() -> None:
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT" in license_text, "LICENSE must be MIT"
    assert "Copyright (c) 2026 Pat Wendorf" in license_text, "LICENSE must name the copyright holder"


def test_changelog_exists_and_nonempty() -> None:
    changelog = REPO_ROOT / "CHANGELOG.md"
    assert changelog.is_file(), "CHANGELOG.md is required"
    assert changelog.read_text(encoding="utf-8").strip(), "CHANGELOG.md must not be empty"


def test_gitignore_exists() -> None:
    assert (REPO_ROOT / ".gitignore").is_file(), ".gitignore is required"


def test_gitignore_is_functional() -> None:
    # A .gitignore that merely exists is not GitHub-ready: it must actually
    # keep the noisy artifacts out of the tree. Assert the common ones.
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("__pycache__/", "*.py[cod]", "*.egg-info/", ".pytest_cache/"):
        assert pattern in gitignore, f".gitignore must ignore {pattern!r}"


def test_readme_is_nonempty_and_describes_the_tool() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.strip(), "README.md must not be empty"
    # The README should describe what the tool does, not be a placeholder.
    lowered = readme.lower()
    assert "pengyos-release" in lowered or "pengyos" in lowered
    assert "publish" in lowered or "feed" in lowered


def test_changelog_uses_keep_a_changelog_format() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.strip(), "CHANGELOG.md must not be empty"
    # Keep a Changelog: entries live under "## [x.y.z]" version headings.
    assert _VERSION_HEADING_RE.search(changelog), (
        "CHANGELOG.md must have at least one '## [x.y.z]' version heading"
    )


def test_readme_and_license_agree_on_copyright_holder() -> None:
    # "Docs are authored — keep them in sync." The README's copyright line must
    # match the LICENSE's; a drift here is a broken GitHub-ready repo.
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    readme_text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    lib_copyright = _copyright_line(license_text)
    readme_copyright = _copyright_line(readme_text)
    assert lib_copyright, "LICENSE must contain a 'Copyright (c) ...' line"
    assert readme_copyright, "README must contain a 'Copyright (c) ...' line"
    assert lib_copyright == readme_copyright, (
        f"README and LICENSE copyright disagree: {readme_copyright!r} vs {lib_copyright!r}"
    )


def test_pyproject_license_matches_licenses_file() -> None:
    import tomllib

    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    license_spec = pyproject["project"]["license"]
    # pyproject uses the SPDX table form { text = "MIT" }.
    if isinstance(license_spec, dict):
        assert license_spec.get("text") == "MIT", "pyproject license must be MIT"
    else:  # SPDX string form e.g. "MIT"
        assert str(license_spec) == "MIT", "pyproject license must be MIT"
    assert "MIT" in (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")


def test_package_imports_with_version() -> None:
    # The package version must match the one declared in pyproject.toml.
    import tomllib

    declared = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    assert pengyos_release.__version__ == declared
    assert pengyos_release.__version__  # non-empty


def test_parser_builds_with_all_subcommands() -> None:
    parser = build_parser()
    action = next(a for a in parser._actions if getattr(a, "dest", None) == "command")
    commands = set(action.choices)
    assert {"publish", "index", "verify", "retain"} <= commands


def test_entry_point_declared_in_pyproject() -> None:
    # The console script must be declared in pyproject.toml.
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert "pengyos-release" in scripts
    assert scripts["pengyos-release"] == "pengyos_release.cli:main"


def test_entry_point_installed() -> None:
    # If the package is installed (editable or otherwise), the console script
    # must resolve to cli:main. Skips when not installed so the suite stays
    # runnable from a bare checkout.
    from importlib.metadata import entry_points, version

    try:
        installed = version("pengyos-release")
    except Exception:
        import pytest

        pytest.skip("pengyos-release is not installed")
    eps = entry_points(group="console_scripts")
    assert "pengyos-release" in {ep.name for ep in eps}
    matched = [ep.value for ep in eps if ep.name == "pengyos-release"]
    assert matched, "console entry point 'pengyos-release' must be registered"
    assert matched[0] == "pengyos_release.cli:main"
