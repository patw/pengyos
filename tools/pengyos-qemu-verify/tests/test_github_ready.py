"""GitHub-ready contract test.

The spec's *GitHub-ready contract* requires that the repository always ships the
files a reviewer (or CI) needs to trust it: a README, an MIT LICENSE with the
project copyright line, a Keep-a-Changelog CHANGELOG, and a .gitignore. This
test asserts that standing invariant so the repo stays GitHub-ready at every
commit — if any of those files disappears or the LICENSE drifts away from the
declared copyright, the suite goes red.

Note: the "full green test suite" half of the contract is the acceptance
condition of the whole run itself (you cannot meaningfully assert your own
greenness from inside the suite), so this test asserts the *files* half.
"""

import re
from pathlib import Path

import pengyos_qemu_verify as pkg

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_FILES = ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore")


def test_all_required_files_exist():
    missing = [name for name in REQUIRED_FILES if not (REPO_ROOT / name).is_file()]
    assert not missing, f"GitHub-ready contract violated, missing: {missing}"


def test_license_is_mit_with_project_copyright():
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    # MIT marker: the short name appears in the header.
    assert "MIT License" in text
    # The standard MIT body is present (spot-check a canonical clause).
    assert "Permission is hereby granted, free of charge" in text
    # The project copyright line is exact per spec.
    assert "Copyright (c) 2026 Pat Wendorf" in text


def test_changelog_is_keep_a_changelog():
    text = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # Keep-a-Changelog structure: a version heading and a dated entry.
    assert re.search(r"^## \[?\d+\.\d+\.\d+", text, re.MULTILINE)
    assert re.search(r"\[?20\d\d-\d\d-\d\d", text)


def test_readme_documents_the_tool():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "pengyos-qemu-verify" in text
    # The README advertises the CLI and how to run it.
    assert "qemu" in text.lower()


def test_gitignore_is_nonempty():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert text.strip(), ".gitignore is empty"


def test_package_version_is_consistent():
    # The package is importable and carries a semver-ish version — a cheap
    # sanity that the repo root really is the project under test.
    assert isinstance(pkg.__version__, str)
    assert len(pkg.__version__.split(".")) >= 3
