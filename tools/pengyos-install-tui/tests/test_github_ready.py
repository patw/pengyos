"""GitHub-ready contract tests (spec.md "GitHub-ready contract").

The repo must be publishable as a clean public project at all times. These
tests assert the *artifacts* of that contract live on disk:

* ``README.md`` — present, non-trivial, and it names the tool and its
  ``install`` command.
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

ROOT = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    path = ROOT / name
    assert path.is_file(), f"missing required file: {name}"
    return path.read_text(encoding="utf-8")


def test_readme_present_and_descriptive():
    text = _text("README.md")
    # Non-trivial and actually about the tool.
    assert len(text) > 200
    assert "pengyos-install-tui" in text
    # The command is documented.
    assert "pengyos-install-tui install" in text
    # The install targets are spelled out.
    assert "Core" in text and "Server" in text
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


def test_readme_documents_the_install_options():
    """The README is the user-facing doc — it must spell out the real CLI."""
    text = _text("README.md")
    for needle in ("--payload", "--plain", "--reboot"):
        assert needle in text, f"README should document the {needle} option"


def test_license_is_exact_mit_body():
    """The LICENSE is the canonical MIT body, not a look-alike stub.

    The coarse check (``test_license_is_mit_with_exact_copyright``) only needs
    a few substrings. Here we normalise whitespace and assert the *entire*
    canonical MIT clause chain — the clauses that make it the MIT license — are
    present verbatim. The body spans multiple lines, so we collapse
    whitespace before matching.
    """
    text = _text("LICENSE")
    collapsed = re.sub(r"\s+", " ", text).strip()
    clauses = [
        'Copyright (c) 2026 Pat Wendorf',
        "Permission is hereby granted, free of charge, to any person obtaining a copy",
        'of this software and associated documentation files (the "Software"), to deal',
        "in the Software without restriction, including without limitation the rights",
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell",
        "copies of the Software, and to permit persons to whom the Software is",
        "furnished to do so, subject to the following conditions:",
        'The above copyright notice and this permission notice shall be included in all',
        "copies or substantial portions of the Software.",
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR',
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,",
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE",
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER",
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,",
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE",
        "SOFTWARE.",
    ]
    for clause in clauses:
        assert clause in collapsed, f"LICENSE missing canonical MIT clause: {clause!r}"


def test_changelog_has_version_footer_links():
    """A Keep a Changelog file links its sections at the bottom.

    The coarse check (``test_changelog_present_and_unreleased_section``) only
    needs a ``[Unreleased]`` heading. A real changelog closes the loop with
    reference-style link definitions — an ``[Unreleased]:`` line plus at least
    one version link (``[0.1.0]:`` or similar) — so a release can bump the
    comparison range.
    """
    text = _text("CHANGELOG.md")
    assert "[Unreleased]:" in text, "CHANGELOG footer is missing the [Unreleased] link"
    # At least one versioned link target, e.g. [0.1.0]: https://...
    assert re.search(r"^\[[\d][^\]]+\]:", text, re.MULTILINE), (
        "CHANGELOG footer is missing a versioned link definition"
    )


def test_pyproject_requires_python_311():
    """The live stick ships Python 3.11 — the tool must not require newer.

    Tests read ``pyproject.toml`` with ``tomllib`` (3.11+), so pinning
    ``requires-python`` to ``>=3.11`` is both a runtime and a test-time
    guarantee. This catches a drift that would silently break the shipped
    stick.
    """
    meta = tomllib.loads(_text("pyproject.toml"))
    req = meta["project"].get("requires-python")
    assert req is not None, "pyproject is missing requires-python"
    assert req == ">=3.11", f"requires-python must be '>=3.11' (the shipped stick), got {req!r}"


def test_version_matches_pyproject():
    """``__version__`` in the package must equal the pyproject version.

    A classic GitHub-ready foot-gun: bump one and forget the other, so
    ``pengyos-install-tui --version`` disagrees with the published metadata.
    This test pins them to the same source of truth.
    """
    init = (ROOT / "pengyos_install_tui" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init)
    assert m, "could not find __version__ in pengyos_install_tui/__init__.py"
    init_version = m.group(1)

    meta = tomllib.loads(_text("pyproject.toml"))
    proj_version = meta["project"]["version"]

    assert init_version == proj_version, (
        f"__version__ ({init_version}) != pyproject version ({proj_version})"
    )
    # And it looks like a version, so a typo can't pass silently.
    assert re.match(r"^\d+\.\d+\.\d+", init_version), (
        f"__version__ {init_version!r} does not look like a version"
    )
