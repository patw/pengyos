"""GitHub-ready contract for pengyos-desktop-net.

The spec's **GitHub-ready contract** is a set of invariants the repo must hold
at all times so it can be pushed to GitHub as a clean, self-describing project:

    README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
    `.gitignore`, and a full green test suite.

This module asserts those invariants. The granular tests give a precise failure
message when one invariant breaks; :func:`test_github_ready` is the master gate
that asserts the whole contract holds together (the `test_github_ready` the spec
names).

The repo is standard-library-only at test time, so this suite needs no network,
root, display, or hardware.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent

# The four GitHub-ready docs, verbatim names.
GITHUB_READY_DOCS = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore")

# The copyright line the MIT LICENSE must carry (spec: "Copyright (c) 2026 Pat
# Wendorf").
COPYRIGHT_LINE = "Copyright (c) 2026 Pat Wendorf"

# The canonical SPDX license identifier this project declares.
LICENSE_ID = "MIT"

# The project version (pyproject) must equal the newest Keep-a-Changelog version.
PYPROJECT_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]

# Keep-a-Changelog: `## [x.y.z] - YYYY-MM-DD`. The newest entry is the first
# header found reading top-to-bottom.
CHANGELOG_VERSION_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def _readme_links() -> list[str]:
    """The relative link targets in README.md (external URLs dropped)."""
    text = (ROOT / "README.md").read_text("utf-8")
    links = re.findall(r"\]\((?P<url>[^)]+)\)", text)
    return [u for u in links if u and not u.startswith(("http://", "https://", "mailto:"))]


def test_github_ready_docs_present_nonempty():
    """The four GitHub-ready docs exist and are non-empty."""
    for name in GITHUB_READY_DOCS:
        path = ROOT / name
        assert path.is_file(), f"{name} is missing"
        assert path.stat().st_size > 0, f"{name} is empty"


def test_readme_present_nonempty():
    """README.md exists and is non-empty."""
    readme = ROOT / "README.md"
    assert readme.is_file()
    assert readme.stat().st_size > 0


def test_license_is_mit_with_copyright():
    """LICENSE is MIT and carries the canonical copyright line."""
    license_text = (ROOT / "LICENSE").read_text("utf-8")
    assert LICENSE_ID in license_text, "LICENSE is not MIT"
    assert COPYRIGHT_LINE in license_text, (
        f"LICENSE is missing the copyright line {COPYRIGHT_LINE!r}"
    )


def test_pyproject_license_matches():
    """pyproject declares the MIT license the LICENSE file carries."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert data["project"]["license"]["text"] == LICENSE_ID


def test_changelog_keep_a_changelog_format():
    """CHANGELOG.md is Keep-a-Changelog: at least one `## [x.y.z]` header."""
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
    assert CHANGELOG_VERSION_RE.search(changelog), (
        "CHANGELOG.md has no `## [x.y.z]` header (Keep-a-Changelog format)"
    )


def test_changelog_newest_matches_pyproject():
    """The newest CHANGELOG version equals the pyproject version."""
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
    versions = CHANGELOG_VERSION_RE.findall(changelog)
    assert versions, "CHANGELOG.md has no `## [x.y.z]` header"
    newest = versions[0]
    assert newest == PYPROJECT_VERSION, (
        f"CHANGELOG newest version {newest!r} != pyproject version {PYPROJECT_VERSION!r}"
    )


def test_readme_cross_links_resolve():
    """Every relative link in README.md resolves to a file that exists."""
    links = _readme_links()
    assert links, "README.md has no relative links to resolve"
    missing = [u for u in links if not (ROOT / u).exists()]
    assert not missing, f"README links to missing files: {missing}"


def test_github_ready():
    """The master GitHub-ready contract: every invariant holds together."""
    test_github_ready_docs_present_nonempty()
    test_readme_present_nonempty()
    test_license_is_mit_with_copyright()
    test_pyproject_license_matches()
    test_changelog_keep_a_changelog_format()
    test_changelog_newest_matches_pyproject()
    test_readme_cross_links_resolve()
