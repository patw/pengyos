"""Shared pytest fixtures for the PengyOS recipe-lint suite.

All fixtures are file-system-local and need no network, root, or hardware.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The committed good/bad recipe fixtures live one level above this tests/ dir.
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GOOD_FIXTURES = FIXTURES_DIR / "good"
BAD_FIXTURES = FIXTURES_DIR / "bad"


@pytest.fixture
def run_cli(monkeypatch):
    """Return a callable that invokes the CLI in-process with a clean env.

    The working directory is pinned to the repo root so relative paths in
    fixtures resolve deterministically.
    """
    from pengyos_recipe_lint.cli import main

    def _run(*args: str) -> int:
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)
        return main(list(args))

    return _run


@pytest.fixture
def recipe_tree(tmp_path: Path) -> Path:
    """Create a small directory tree of recipes and return its root.

    Layout::

        root/
          good.mk            (+ good.hash)
          sub/
          bare.mk            (no hash)
          nested/
            deep.mk          (+ deep.hash)
    """
    root = tmp_path / "recipes"
    sub = root / "sub"
    nested = root / "nested"
    sub.mkdir(parents=True)
    nested.mkdir(parents=True)

    (root / "good.mk").write_text(
        'GOOD_VERSION = "1.0"\nGOOD_SITE = https://example.com/good\n',
        encoding="utf-8",
    )
    (root / "good.hash").write_text("abcd  good\n", encoding="utf-8")

    (sub / "bare.mk").write_text('BARE_VERSION = "2.0"\n', encoding="utf-8")

    (nested / "deep.mk").write_text('DEEP_VERSION = "3.0"\n', encoding="utf-8")
    (nested / "deep.hash").write_text("efgh  deep\n", encoding="utf-8")

    return root


@pytest.fixture
def compliant_tree(tmp_path: Path) -> Path:
    """Create a directory tree of fully-compliant recipes and return its root.

    Every recipe here passes all four rules (version pinned, a ``sha256`` for
    its source, a declared license, and an ``https`` download site), so
    ``check`` on this tree exits ``0``.
    Layout::

        root/
          good.mk            (+ good.hash)
          nested/
            deep.mk          (+ deep.hash)
    """
    root = tmp_path / "recipes"
    nested = root / "nested"
    nested.mkdir(parents=True)

    good = root / "good.mk"
    good.write_text(
        'GOOD_VERSION = "1.0"\n'
        'GOOD_SITE = https://example.com/good\n'
        'GOOD_SOURCE = good-1.0.tar.gz\n'
        "GOOD_LICENSE = MIT\n",
        encoding="utf-8",
    )
    good.with_suffix(".hash").write_text(
        "sha256  " + "a" * 64 + "  good-1.0.tar.gz\n", encoding="utf-8"
    )

    deep = nested / "deep.mk"
    deep.write_text(
        'DEEP_VERSION = "3.0"\n'
        'DEEP_SITE = https://example.com/deep\n'
        'DEEP_SOURCE = deep-3.0.tar.gz\n'
        "DEEP_LICENSE = Apache-2.0\n",
        encoding="utf-8",
    )
    deep.with_suffix(".hash").write_text(
        "sha256  " + "b" * 64 + "  deep-3.0.tar.gz\n", encoding="utf-8"
    )

    return root


@pytest.fixture
def good_fixtures() -> Path:
    """The committed ``fixtures/good`` tree of fully-compliant recipes."""
    assert GOOD_FIXTURES.is_dir(), GOOD_FIXTURES
    return GOOD_FIXTURES


@pytest.fixture
def bad_fixtures() -> Path:
    """The committed ``fixtures/bad`` tree of recipes that break rules."""
    assert BAD_FIXTURES.is_dir(), BAD_FIXTURES
    return BAD_FIXTURES
