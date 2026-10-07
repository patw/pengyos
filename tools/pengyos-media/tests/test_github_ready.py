"""GitHub-ready contract tests (spec.md "GitHub-ready contract").

The repo must be publishable as a clean public project at all times. These
tests assert the *artifacts* of that contract live on disk:

* ``README.md`` — present, non-trivial, and it names the tool + all three
  subcommands.
* ``LICENSE`` — MIT, and carrying the exact ``Copyright (c) 2026 Pat Wendorf``
  line the spec pins.
* ``CHANGELOG.md`` — present, with a ``[Unreleased]`` section (Keep a
  Changelog shaped).
* ``.gitignore`` — present, and it ignores the usual build/test/editor noise.
* ``pyproject.toml`` — declares the MIT license (text + classifier) and points
  ``readme`` at ``README.md``.

The contract also demands "a full green test suite". The loop's objective
function already *is* "``pytest -q`` exits 0", and this test participates in
that run. To add an in-test guard that a broken test module cannot slip into a
"GitHub-ready" commit, ``test_test_suite_collectable`` runs
``pytest --collect-only`` in a subprocess. Collect-only imports and validates
every test module (reporting the per-file test counts) without *executing* any
test function, so it cannot recurse back into this very test — it is the
strongest hermetic self-check available without fragile nested execution.
"""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    path = ROOT / name
    assert path.is_file(), f"missing required file: {name}"
    return path.read_text(encoding="utf-8")


def test_readme_present_and_descriptive():
    text = _text("README.md")
    # Non-trivial and actually about the tool.
    assert len(text) > 200
    assert "pengyos-media" in text
    # All three subcommands are documented.
    assert "plan" in text and "build" in text and "lint" in text
    # Points at the spec/implementation docs (docs kept in sync).
    assert "spec.md" in text


def test_license_is_mit_with_exact_copyright():
    text = _text("LICENSE")
    assert "MIT License" in text
    # The exact copyright line the spec pins.
    assert "Copyright (c) 2026 Pat Wendorf" in text
    # It is the real MIT body, not a stub: it carries the standard warranty
    # disclaimer.
    assert "WITHOUT WARRANTY" in text


def test_changelog_present_and_unreleased_section():
    text = _text("CHANGELOG.md")
    assert "Changelog" in text
    assert "## [Unreleased]" in text
    # Keep a Changelog shaped.
    assert "keepachangelog.com" in text
    # At least one of the standard section headings is present.
    assert re.search(r"^### (Added|Changed|Fixed|Removed|Deprecated|Security)\b",
                     text, re.MULTILINE)


def test_gitignore_present_and_sane():
    text = _text(".gitignore")
    for needle in ("__pycache__/", "*.py[cod]", "*.egg-info/", ".pytest_cache/"):
        assert needle in text, f".gitignore should ignore {needle}"


def test_pyproject_declares_mit_and_readme():
    meta = tomllib.loads(_text("pyproject.toml"))
    proj = meta["project"]
    # readme points at the README file (relative to pyproject).
    assert proj.get("readme") == "README.md"
    # MIT, declared both as a text field and as an OSI classifier.
    lic = proj.get("license")
    assert lic is not None
    lic_text = lic.get("text") if isinstance(lic, dict) else lic
    assert lic_text == "MIT"
    assert "License :: OSI Approved :: MIT License" in proj.get("classifiers", [])


def test_test_suite_collectable():
    """The whole test module set imports and collects with no errors.

    Runs ``pytest --collect-only`` in a subprocess: this validates that every
    test module is importable and has no collection-time (import/syntax/fixture)
    errors. It does not *execute* tests, so it cannot recurse into this test.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"pytest --collect-only failed (rc={proc.returncode}):\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    # ``-q`` (from addopts) renders collect-only as per-file counts, e.g.
    # ``tests/test_plan.py: 85``. Sum them: we must have collected a healthy
    # number of tests, and this very module must be part of the collected set.
    assert "test_github_ready" in proc.stdout, f"this module not collected:\n{proc.stdout}"
    counts = [int(n) for n in re.findall(r"^\S+\.py:\s*(\d+)\s*$",
                                         proc.stdout, re.MULTILINE)]
    assert counts, f"no per-file collection counts found:\n{proc.stdout}"
    assert sum(counts) > 0
