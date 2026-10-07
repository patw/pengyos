"""GitHub-ready contract for the PengyOS server-monitoring repo (US-SRV-3).

This is the single gate that asserts the whole repository is GitHub-ready, as
specified in ``spec.md``:

    GitHub-ready contract (assert with a ``test_github_ready``):
    README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
    ``.gitignore``, and a full green test suite. Docs are authored — keep them
    in sync.

Everything here is stdlib-only and offline. The only network/root/hardware
touch would be the optional subprocess run of the offline suite (guarded by an
environment variable so it never recurses into itself).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pengyos_monitoring

REPO_ROOT = Path(__file__).resolve().parents[1]

# Artifacts the GitHub-ready contract requires to be present and non-empty.
_REQUIRED_ARTIFACTS = ("README.md", "CHANGELOG.md", ".gitignore", "LICENSE")

# Markers the .gitignore must cover (Python artefacts the suite produces).
_GITIGNORE_MARKERS = ("__pycache__", "*.py[cod]", ".pytest_cache/")

# A version entry in a Keep-a-Changelog CHANGELOG looks like "## [0.1.0] — …".
_VERSION_ENTRY_RE = re.compile(r"^## \[[^\]]+\]", re.MULTILINE)

# Each top-level entry rendered in the README tree (the "├── / └──" lines).
_TREE_ENTRY_RE = re.compile(r"^[├└]─\s+(?P<entry>\S+?)(?:\s+#.*)?$")

# Env var that stops the self-check subprocess from recursing into itself.
_SELF_CHECK_GUARD = "PENGYOS_GITHUB_READY_SELF"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _top_level_entries_from_readme() -> list[str]:
    """The top-level paths listed in the README's layout tree."""
    text = _read(REPO_ROOT / "README.md")
    entries = []
    for line in text.splitlines():
        m = _TREE_ENTRY_RE.match(line)
        if m:
            entries.append(m.group("entry").rstrip("/"))
    return entries


def test_github_ready() -> None:
    """The whole repo is GitHub-ready: docs present/correct and suite green."""
    # 1. Required artifacts are present and non-empty.
    for name in _REQUIRED_ARTIFACTS:
        path = REPO_ROOT / name
        assert path.exists(), f"{name} is missing — not GitHub-ready"
        assert path.stat().st_size > 0, f"{name} is empty — not GitHub-ready"

    # 2. LICENSE is MIT and carries the required attribution.
    license_text = _read(REPO_ROOT / "LICENSE")
    assert "MIT License" in license_text, "LICENSE is not an MIT license"
    assert (
        "Copyright (c) 2026 Pat Wendorf" in license_text
    ), "LICENSE is missing the required copyright attribution"

    # 3. CHANGELOG documents at least one released version.
    changelog = _read(REPO_ROOT / "CHANGELOG.md")
    assert _VERSION_ENTRY_RE.search(changelog), (
        "CHANGELOG.md has no version entries (e.g. '## [0.1.0] — …')"
    )

    # 4. .gitignore covers the artefacts the Python suite produces.
    gitignore = _read(REPO_ROOT / ".gitignore")
    for marker in _GITIGNORE_MARKERS:
        assert marker in gitignore, f"{marker} should be in .gitignore"

    # 5. pyproject.toml is valid, stdlib-only, and pytest-configured.
    data = tomllib.loads(_read(REPO_ROOT / "pyproject.toml"))
    project = data["project"]
    assert project["name"] == "pengyos-server-monitoring"
    assert project["requires-python"].startswith(">=")
    assert project.get("dependencies", []) == [], (
        "runtime dependencies must be empty (stdlib only by design)"
    )
    extras = data["project"].get("optional-dependencies", {})
    assert set(extras) <= {"test"}, "only the 'test' extra is allowed"
    pytest_cfg = data.get("tool", {}).get("pytest", {}).get("ini_options")
    assert pytest_cfg is not None, "pytest must be configured in pyproject.toml"
    assert pytest_cfg.get("testpaths") == ["tests"]

    # 6. The evaluator package imports cleanly and its version is pinned.
    assert pengyos_monitoring.__version__ == "0.1.0"

    # 7. Docs are in sync: every top-level entry in the README tree exists.
    for entry in _top_level_entries_from_readme():
        assert (REPO_ROOT / entry).exists(), (
            f"README layout lists {entry!r} but it is missing — docs out of sync"
        )

    # 8. The offline test suite is green. Guarded by an env var so this check
    #    never recurses into itself when it is the one running.
    if os.environ.get(_SELF_CHECK_GUARD):
        return
    _assert_suite_is_green()


def _assert_suite_is_green() -> None:
    """Run the offline suite via subprocess and require exit code 0."""
    env = {**os.environ, _SELF_CHECK_GUARD: "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, (
        "GitHub-ready contract requires a green test suite, but pytest exited "
        f"{proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    )
