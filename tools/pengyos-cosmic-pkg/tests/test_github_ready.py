"""GitHub-ready contract for the pengyos-cosmic-pkg repo.

``spec.md`` defines a **GitHub-ready contract** that must be asserted with a
``test_github_ready``::

    README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
    `.gitignore`, and a full green test suite. Docs are authored — keep them in
    sync.

This module asserts the file artifacts (the suite's own greenness is the verify
step's job — nothing here re-invokes pytest) and that the packaged deliverable
imports and exposes its CLI entry point, so a freshly-cloned checkout is
publishable and runnable before a single feature is even exercised.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

# Repo root = parent of this tests/ dir.
ROOT = Path(__file__).resolve().parent.parent


def _require(name: str) -> Path:
    """Return a present, non-empty repo artifact, failing loudly otherwise."""
    path = ROOT / name
    assert path.is_file(), f"GitHub-ready artifact missing: {name}"
    assert path.read_text(encoding="utf-8").strip(), f"{name} is empty"
    return path


def _implemented_subcommands() -> set[str]:
    """The subcommand surface exposed by the CLI (the source of truth).

    Reads the registered subparsers directly from ``build_parser`` so the check
    reflects the real public contract rather than a hand-maintained list.
    """
    import argparse

    from pengyos_cosmic_pkg.cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices.keys())
    raise AssertionError("the CLI registers no subcommands")


def _documented_subcommands(readme_text: str) -> set[str]:
    """Subcommand names the README documents, parsed from its command table.

    The README's "What it does" table lists one subcommand per row in the first
    column (a backtick-quoted identifier). This parses exactly those, so the
    check is robust to prose in the Usage section and ignores the unrelated
    exit-code table (whose first column is a digit, not a letter).
    """
    found: set[str] = set()
    for line in readme_text.splitlines():
        match = re.match(r"^\|\s+`([a-z][a-z0-9_-]*)`\s*\|", line)
        if match:
            found.add(match.group(1))
    return found


def test_github_ready():
    """The repo ships the GitHub-ready artifacts with correct metadata.

    Asserts the contract from ``spec.md``: README, LICENSE (MIT, credited to
    Pat Wendorf), CHANGELOG and ``.gitignore`` all authored and non-empty, plus
    ``pyproject.toml`` that wires the ``pengyos-cosmic-pkg`` console script.
    """
    # README documents the tool (not a stub) and stays in sync with the spec.
    readme = _require("README.md").read_text(encoding="utf-8")
    assert "pengyos-cosmic-pkg" in readme.lower()

    # LICENSE is MIT and credits the right holder — every time.
    license_text = _require("LICENSE").read_text(encoding="utf-8")
    assert "MIT" in license_text
    assert "Copyright (c) 2026 Pat Wendorf" in license_text

    # CHANGELOG and .gitignore are authored.
    _require("CHANGELOG.md")
    _require(".gitignore")

    # Package metadata is present and exposes the documented console script.
    with (ROOT / "pyproject.toml").open("rb") as fh:
        meta = tomllib.load(fh)
    scripts = meta["project"]["scripts"]
    assert scripts == {
        "pengyos-cosmic-pkg": "pengyos_cosmic_pkg.cli:main",
    }
    # The documented runtime constraint holds.
    assert meta["project"]["requires-python"] == ">=3.11"


def test_github_ready_package_imports():
    """The packaged deliverable imports and exposes its CLI entry point."""
    cli = importlib.import_module("pengyos_cosmic_pkg.cli")
    assert callable(cli.main)


def test_github_ready_cli_runs_without_install():
    """`python -m pengyos_cosmic_pkg` works from a clean checkout (no install).

    The README documents running without installing; this smoke test pins that
    promise so the entry point can't silently rot.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_cosmic_pkg", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "pengyos-cosmic-pkg" in proc.stdout


def test_github_ready_docs_in_sync():
    """The documented subcommand surface matches the implemented one.

    ``spec.md``'s GitHub-ready contract requires the docs to be "authored — keep
    them in sync." This is **bidirectional**, so neither drift is possible:

    * implemented -> documented: every subcommand the CLI exposes is written up
      in the README (nothing ships undocumented);
    * documented -> implemented: every subcommand the README documents is a real,
      importable feature module with a ``main`` entry point (nothing is
      documented that does not exist).

    The CLI parser is the source of truth for what is implemented; the README's
    command table is the source for what is documented.
    """
    readme = _require("README.md").read_text(encoding="utf-8")
    implemented = _implemented_subcommands()
    documented = _documented_subcommands(readme)

    # implemented -> documented: nothing shipped without docs.
    undocumented = sorted(implemented - documented)
    assert not undocumented, (
        f"implemented subcommand(s) not documented in README: {undocumented}"
    )

    # documented -> implemented: nothing documented without code.
    unimplemented = sorted(documented - implemented)
    assert not unimplemented, (
        f"documented subcommand(s) have no implementation: {unimplemented}"
    )

    # The documented surface is non-empty and each entry exposes an entry point.
    assert documented, "the README documents no subcommands"
    for name in documented:
        module = importlib.import_module(f"pengyos_cosmic_pkg.{name}")
        assert callable(getattr(module, "main", None)), (
            f"documented subcommand '{name}' has no main() entry point"
        )
