"""GitHub-ready contract.

The repository must always ship with the files and metadata a consumer expects
to find on GitHub: a README, a correct MIT LICENSE, a CHANGELOG, a `.gitignore`,
and a green test suite (this very file collecting and passing).

These checks are intentionally strict so the "GitHub-ready" invariant can never
rot silently.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LICENSE_COPYRIGHT = "Copyright (c) 2026 Pat Wendorf"


def _read(name: str) -> str:
    path = ROOT / name
    assert path.is_file(), f"missing GitHub-required file: {name}"
    return path.read_text(encoding="utf-8")


def test_readme_present() -> None:
    readme = _read("README.md")
    assert "pengyos-podman" in readme
    assert "LICENSE" in readme  # references the license


def test_license_present_and_mit() -> None:
    text = _read("LICENSE")
    assert "MIT" in text, "LICENSE must be the MIT license"
    assert LICENSE_COPYRIGHT in text, f"LICENSE must carry {LICENSE_COPYRIGHT!r}"


def test_changelog_present() -> None:
    changelog = _read("CHANGELOG.md")
    assert "# Changelog" in changelog
    assert "0.1.0" in changelog


def test_gitignore_present() -> None:
    gitignore = _read(".gitignore")
    assert "__pycache__/" in gitignore


def test_pyproject_declares_project() -> None:
    pyproject = _read("pyproject.toml")
    assert "pengyos-podman" in pyproject


def test_package_layout() -> None:
    pkg = ROOT / "pengyos_podman"
    assert pkg.is_dir(), "package directory pengyos_podman is missing"
    for name in ("__init__.py", "cli.py", "__main__.py"):
        assert (pkg / name).is_file(), f"{name} is missing from the package"


# --- Cross-consistency (docs kept in sync) ----------------------------------
#
# The GitHub-ready contract requires docs to be *in sync* with the code — the
# checks above only prove the files *exist*. The following catch the "docs rot
# silently" failure mode by asserting the metadata across README / LICENSE /
# CHANGELOG / .gitignore / pyproject is mutually consistent, not just present.


def _pyproject() -> dict:
    """Parse ``pyproject.toml`` (stdlib ``tomllib``) and return the mapping."""

    import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _author_names(pyproject: dict) -> list[str]:
    """Return the declared author names from ``pyproject.toml``."""

    authors = pyproject.get("project", {}).get("authors", [])
    return [a.get("name", "") for a in authors if a.get("name")]


def _subcommand_names() -> list[str]:
    """Return the CLI subcommand names, driven by the real argparse parser.

    Driven by the actual parser (not hardcoded) so a subcommand added to code
    but not documented in the README is caught.
    """

    import argparse

    from pengyos_podman.cli import build_parser

    parser = build_parser()
    actions = [
        a
        for a in parser._subparsers._actions
        if isinstance(a, argparse._SubParsersAction)
    ]
    if not actions:
        return []
    return list(actions[0].choices.keys())


def test_license_is_full_mit_text() -> None:
    # Not just the word "MIT" — the canonical MIT body, so a stub LICENSE that
    # merely mentions MIT can't pass. The phrases are chosen to be contiguous in
    # the standard MIT text (which otherwise wraps "THE\\nAUTHORS" across lines).
    text = _read("LICENSE")
    for fragment in (
        "Permission is hereby granted, free of charge",
        "THE SOFTWARE IS PROVIDED",
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE",
    ):
        assert fragment in text, f"LICENSE missing canonical MIT fragment: {fragment!r}"


def test_license_copyright_matches_pyproject_authors() -> None:
    # The copyright holder in LICENSE must match the author(s) declared in
    # pyproject.toml — a LICENSE that drifts from the declared author is caught.
    authors = _author_names(_pyproject())
    assert authors, "pyproject.toml must declare at least one author"
    license_text = _read("LICENSE")
    for name in authors:
        assert name in license_text, (
            f"LICENSE must name the declared author {name!r}"
        )


def test_changelog_has_current_version() -> None:
    # The version declared in pyproject.toml must appear in CHANGELOG.md — a
    # version bump that isn't documented is caught.
    version = _pyproject()["project"]["version"]
    changelog = _read("CHANGELOG.md")
    assert version in changelog, (
        f"CHANGELOG.md must document version {version!r} from pyproject.toml"
    )


def test_readme_documents_all_subcommands() -> None:
    # The README must document every CLI subcommand — an undocumented command
    # (a common "docs drifted" failure) is caught.
    readme = _read("README.md")
    for subcommand in _subcommand_names():
        assert subcommand in readme, (
            f"README.md must document the {subcommand!r} subcommand"
        )


def test_gitignore_ignores_generated_units() -> None:
    # Emitted units must be git-ignored so a stray generated unit never gets
    # committed.
    gitignore = _read(".gitignore")
    assert "units" in gitignore, ".gitignore must ignore the generated units/ dir"
    assert "*.quadlet" in gitignore, ".gitignore must ignore emitted .quadlet units"


def test_github_ready() -> None:
    """Aggregated contract check: every artifact a GitHub repo should ship.

    Also runs the whole suite (minus itself) in a fresh subprocess so a green
    test_github_ready proves the repo is genuinely GitHub-ready.
    """

    for name in ["README.md", "LICENSE", "CHANGELOG.md", ".gitignore", "pyproject.toml"]:
        _read(name)

    assert (ROOT / "pengyos_podman" / "__init__.py").is_file()

    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-k", "not test_github_ready"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-2000:]
    assert proc.returncode == 0, (
        f"GitHub-ready check failed: test suite is not green "
        f"(return code {proc.returncode})\n{tail}"
    )
