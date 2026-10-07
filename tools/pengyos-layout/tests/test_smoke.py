# Smoke tests for the pengyos_layout scaffold.

import os
import subprocess
import sys

import pengyos_layout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_package_has_version():
    assert isinstance(pengyos_layout.__version__, str)
    assert pengyos_layout.__version__.count(".") >= 2


def test_cli_version_flag():
    # The subprocess is a fresh interpreter; point it at the src layout the same
    # way `pip install -e .` would, without touching the system Python.
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO_ROOT, "src"))
    out = subprocess.run(
        [sys.executable, "-m", "pengyos_layout", "--version"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, out.stderr
    assert pengyos_layout.__version__ in out.stdout


def test_github_ready():
    """The GitHub-ready contract (see spec.md)."""
    for name in ("README.md", "LICENSE", "CHANGELOG.md", ".gitignore"):
        path = os.path.join(REPO_ROOT, name)
        assert os.path.isfile(path), f"missing GitHub-ready file: {name}"

    with open(os.path.join(REPO_ROOT, "LICENSE"), encoding="utf-8") as fh:
        license_text = fh.read()
    assert "Copyright (c) 2026 Pat Wendorf" in license_text
    assert license_text.strip().startswith("MIT License")
