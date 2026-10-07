"""Shared pytest fixtures for the pengyos-release test suite.

All fixtures are local and offline. The fake build output is a **committed
fixture** (``tests/fixtures/sample-build/``) — a real, read-only directory that
ships in the repo. Grounding the suite in a fixed build output makes the
``publish`` path *golden*: the same input always produces byte-identical output,
so the tests exercise the real layout/compression/signing paths without touching
a real build or the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_release.signing import StubSigner

# The artifact names the release tool is expected to collect from a build output.
ARTIFACTS = ("rootfs.img", "uki.efi", "sbom.json", "attestation.json", "repro-report.txt")

# The committed, read-only build-output fixture. Grounding the suite in a fixed
# directory (rather than generating bytes in tmp_path) makes publish golden:
# the same input always produces byte-identical output.
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
COMMITTED_BUILD_OUTPUT = FIXTURES_DIR / "sample-build"


@pytest.fixture
def signer() -> StubSigner:
    """A deterministic, offline signer for tests."""
    return StubSigner()


@pytest.fixture
def build_output() -> Path:
    """The committed pengyos-build output fixture (read-only).

    Mirrors what the build emits: a ``images/`` dir with the rootfs and UKI,
    plus the provenance files at the top level. Its contents are fixed, so
    publishing it is deterministic (the "golden" publish).
    """
    return COMMITTED_BUILD_OUTPUT
