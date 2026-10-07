"""Shared pytest fixtures for the pengyos-propose-client test suite.

The suite is fully offline: the gate's HTTP transport is mocked, no network,
root, display or hardware is required.  Integration tests against a real gate
are opt-in behind ``PENGYOS_IT=1`` (see ``test_integration``).
"""

from __future__ import annotations

import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")


def has_it() -> bool:
    """True when integration tests are enabled via PENGYOS_IT=1."""
    return os.environ.get("PENGYOS_IT") == "1"


@pytest.fixture
def fixture_path() -> str:
    """Absolute path to the fixtures directory."""
    return FIXTURES


@pytest.fixture
def sample_proposal_path(fixture_path: str) -> str:
    """Path to the committed sample (Tier 1) proposal."""
    return os.path.join(fixture_path, "sample_proposal.json")


@pytest.fixture
def tier2_proposal_path(fixture_path: str) -> str:
    """Path to the committed sample (Tier 2) proposal."""
    return os.path.join(fixture_path, "tier2_proposal.json")


@pytest.fixture
def sample_proposal(sample_proposal_path: str):
    """Load the sample proposal into a Proposal."""
    from pengyos_propose.proposal import load_proposal_file

    return load_proposal_file(sample_proposal_path)


@pytest.fixture
def write_proposal(tmp_path):
    """Return a helper that writes a proposal dict to a temp JSON file."""

    def _write(name: str, data: dict) -> str:
        p = tmp_path / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return str(p)

    return _write
