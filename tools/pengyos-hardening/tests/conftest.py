"""Shared pytest fixtures and helpers for the pengyos_hardening suite.

The test-suite is the objective function for this repo. Everything here is
stdlib-only and hermetic (no network, no root, no real hardware).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# --- repository layout -----------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent

TESTS_DIR = ROOT / "tests"

# Committed fixture trees live under tests/fixtures. The hardened tree is the
# "everything correct" baseline; each sibling is a single-rule mutation of it.
FIXTURES_DIR = TESTS_DIR / "fixtures"
HARDENED_TREE = FIXTURES_DIR / "hardened"

# Single-rule mutation trees, one per rule id (e.g. ``mutations/ssh-permit-root-login``).
MUTATIONS_DIR = FIXTURES_DIR / "mutations"

PLACEHOLDER_MARKERS = ("TODO", "FIXM" + "E", "XXX", "ST" + "UB")


def integration_enabled() -> bool:
    """Integration tests are opt-in behind PENGYOS_IT=1."""
    return os.environ.get("PENGYOS_IT") == "1"


@pytest.fixture
def root_path() -> Path:
    """Absolute path to the repository root."""
    return ROOT


@pytest.fixture
def hardened_tree() -> Path:
    """The committed hardened fixture tree (audits clean)."""
    return HARDENED_TREE


@pytest.fixture
def fixtures_dir() -> Path:
    """The directory of committed fixture trees."""
    return FIXTURES_DIR


@pytest.fixture
def mutations_dir() -> Path:
    """The directory of single-rule mutation fixture trees."""
    return MUTATIONS_DIR


def mutation_tree(rule_id: str) -> Path:
    """The committed single-rule mutation tree for ``rule_id``.

    Each mutation is a full copy of the hardened tree with exactly one line
    changed so that ``rule_id`` fires (and only it).
    """
    path = MUTATIONS_DIR / rule_id
    if not path.is_dir():
        raise FileNotFoundError(
            f"no mutation fixture for {rule_id!r} under {MUTATIONS_DIR}"
        )
    return path
