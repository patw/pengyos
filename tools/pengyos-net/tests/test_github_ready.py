"""GitHub-ready contract (spec.md) asserted as tests.

Per the spec's "GitHub-ready contract" the repo must, at all times:
- ship README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"),
  CHANGELOG.md and .gitignore,
- keep the docs in sync with what the repo actually contains, and
- have a full green test suite.

These tests check the contract against the files on disk. The
"green suite" check re-runs the whole suite in a subprocess, excluding
this very file to avoid infinite recursion.
"""
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

README = os.path.join(REPO_ROOT, "README.md")
LICENSE = os.path.join(REPO_ROOT, "LICENSE")
CHANGELOG = os.path.join(REPO_ROOT, "CHANGELOG.md")
GITIGNORE = os.path.join(REPO_ROOT, ".gitignore")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- README

def test_readme_present_with_project_title():
    text = _read(README)
    assert "pengyos-net" in text.splitlines()[0]


def test_readme_documents_the_validator():
    text = _read(README)
    assert "validate_ruleset" in text
    assert "pengyos_net" in text


def test_readme_documents_the_shipped_configs():
    text = _read(README)
    for name in ("10-default.network", "20-pengyos-test.netdev",
                 "90-pengyos.conf", "pengyos.nft"):
        assert name in text, f"README does not mention {name}"


def test_readme_documents_policy_and_test_run():
    text = _read(README)
    assert "policy drop" in text  # default-deny inbound is the core policy
    assert "PENGYOS_IT=1" in text  # opt-in integration tests
    assert "pytest" in text  # how to run the suite


def test_readme_relative_links_resolve():
    """Every local link the README points at must exist (docs in sync)."""
    text = _read(README)
    links = re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)
    local = [
        target.split("#", 1)[0]
        for target in links
        if target and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)
    ]
    assert local, "expected the README to contain at least one local link"
    for target in local:
        resolved = os.path.normpath(os.path.join(REPO_ROOT, target))
        assert os.path.exists(resolved), f"README link target missing: {target}"


# ---------------------------------------------------------------- LICENSE

def test_license_is_mit_with_exact_copyright():
    text = _read(LICENSE)
    assert "MIT License" in text
    assert "Copyright (c) 2026 Pat Wendorf" in text


# ---------------------------------------------------------------- CHANGELOG

def test_changelog_has_unreleased_and_records_deliverables():
    text = _read(CHANGELOG)
    assert "## [Unreleased]" in text
    # Each shipped deliverable must be recorded so the changelog stays
    # in sync with the repo contents.
    for name in ("nft_validate.py", "pengyos.nft", "10-default.network",
                 "90-pengyos.conf", ".gitignore"):
        assert name in text, f"CHANGELOG does not record {name}"


# ---------------------------------------------------------------- .gitignore

def test_gitignore_covers_python_noise():
    text = _read(GITIGNORE)
    for pattern in ("__pycache__/", "*.py[cod]", ".pytest_cache/",
                    "*.egg-info/"):
        assert re.search(rf"^{re.escape(pattern)}$", text, re.M), (
            f".gitignore is missing {pattern!r}")


# ---------------------------------------------------------------- green suite

def test_full_suite_is_green():
    """Re-run the whole suite (minus this file) and require zero failures.

    This is the objective function for the GitHub-ready contract: the
    repo is not ready until *every* other test passes.
    """
    this_file = os.path.abspath(__file__)
    cmd = [
        sys.executable, "-m", "pytest", "-q",
        "--ignore", this_file,
        "-p", "no:cacheprovider",
    ]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "nested pytest suite failed:\n"
        f"stdout:\n{proc.stdout[-4000:]}\n"
        f"stderr:\n{proc.stderr[-2000:]}"
    )
