"""Pytest configuration for pengyos-witness-bundle.

Ensures the tool directory (where ``pengyos_witness_bundle`` lives) is importable
regardless of where pytest is invoked from, then enables opt-in integration tests
behind ``PENGYOS_IT=1``.

The build-dir fixtures below are the reusable fixtures the whole suite leans on:

* :func:`good_build`      — a minimal, complete build (all four canonical
  artifacts present) -> witness-ready -> **PASS**;
* :func:`signed_build`    — a good build whose SBOM is signed with a real ed25519
  key -> verifies with ``--pub`` -> **PASS**;
* :func:`tampered_build`  — a good build whose SBOM is corrupted *after* signing ->
  the signature no longer matches -> **FAIL**;
* :func:`partial_build`   — an incomplete build (only SBOM + signature) -> every
  other kind missing -> **FAIL**;
* :func:`empty_build`     — an empty dir -> every artifact missing -> **FAIL**;
* :func:`pub`             — the ``signer``'s public key written to a file, for
  ``--pub``.

They live here (not inline in each test module) so every test can share one
canonical way to stand up a build, and so the PASS / FAIL fixtures are exercised
in one dedicated group (see ``tests/test_fixtures.py``).
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Make the package importable when running `pytest` from the tool directory.
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path:
    sys.path.insert(0, _TOOL_DIR)

from pengyos_witness_bundle import sigverify as _sv  # noqa: E402


def integration_enabled() -> bool:
    """Integration tests are opt-in behind ``PENGYOS_IT=1``."""
    return os.environ.get("PENGYOS_IT") == "1"


# Exposed so tests can query the flag without importing os themselves.
INTEGRATION_ENABLED = integration_enabled()


class Signer:
    """A (seed, public key) pair plus a ``sign`` helper for the SBOM bytes."""

    def __init__(self, seed: bytes, pub: bytes):
        self.seed = seed
        self.pub = pub

    def sign(self, data: bytes) -> bytes:
        return _sv.sign_bytes(data, self.seed)


@pytest.fixture
def signer():
    """A fresh random 32-byte seed / derived public key pair.

    The seed is drawn once and the public key is derived from it — a single
    ``os.urandom(32)`` call, not two (which would pair an unrelated key).
    """
    seed = os.urandom(32)
    return Signer(seed, _sv._publickey(seed))


@pytest.fixture
def spdx_doc():
    """A minimal but well-formed SPDX 2.3 document."""
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "creationInfo": {
            "created": "2026-09-17T17:57:36+00:00",
            "creators": ["Tool:pengyos-sbom/0.1.0"],
        },
        "dataLicense": "CC0-1.0",
        "documentDescribes": ["SPDXRef-RootArtifact"],
        "documentNamespace": "https://pengyos.catbee.ca/sbom/test",
        "packages": [
            {
                "SPDXID": "SPDXRef-Package",
                "name": "pengyos-core",
                "versionInfo": "0.1.0",
                "downloadLocation": "NOASSERTION",
            }
        ],
    }


# --- shared build-dir fixtures -----------------------------------------------
#
# The reusable fixtures the whole suite leans on. A build dir is always a
# *complete* four-artifact pengyos-build output written under ``tmp_path``:
#
#   images/<target>.spdx.json        the SBOM            (also <target>.spdx.json.sig)
#   images/<target>.cyclonedx.json   the CycloneDX SBOM
#   repro-report.txt                 the repro report
#
# The SBOM carries a dummy (non-verifying) signature by default, so every kind is
# present and the build is witness-ready with no --pub. When signed, the SBOM is
# signed with the caller's ``signer``; when tampered, the body is corrupted AFTER
# signing so the signature no longer matches.

# The canonical target name pengyos-build emits for this build.
_GOOD_TARGET = "pengyos-x86_64"
# The (arbitrary) SBOM body written for unsigned / good builds.
_GOOD_SBOM = b'{"SPDXID":"SPDXRef-DOCUMENT","spdxVersion":"SPDX-2.3","dataLicense":"CC0-1.0"}'


def _write_build(
    root: Path,
    *,
    signed: bool = False,
    tamper: bool = False,
    signer=None,
) -> Path:
    """Write a complete four-artifact build dir under *root* and return it.

    * *signed*  — the SBOM is signed with *signer* (and its ``.sig`` written);
    * *tamper*  — when combined with *signed*, the on-disk SBOM body is corrupted
      after signing, so the signature no longer matches.

    Without *signed* the SBOM carries a dummy signature: all four kinds are
    present, so the build is witness-ready with no --pub key.
    """
    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    sbom = images / f"{_GOOD_TARGET}.spdx.json"
    body = _GOOD_SBOM
    if signed:
        assert signer is not None, "a signed build requires the 'signer' fixture"
        signature = signer.sign(body)
        if tamper:
            # Corrupt the body AFTER signing so the signature no longer matches.
            body = body.replace(b"CC0-1.0", b"CC0-9.9")
        sbom.write_bytes(body)
        (images / f"{_GOOD_TARGET}.spdx.json.sig").write_bytes(signature)
    else:
        sbom.write_bytes(body)
        (images / f"{_GOOD_TARGET}.spdx.json.sig").write_bytes(b"dummy-sig")
    (images / f"{_GOOD_TARGET}.cyclonedx.json").write_text("{}")
    (root / "repro-report.txt").write_text("repro report")
    return root


@pytest.fixture
def good_build(tmp_path) -> Path:
    """A minimal, complete build dir: all four canonical artifacts present.

    The SBOM carries a dummy (non-verifying) signature so every kind is present —
    witness-ready with no --pub key -> **PASS**.
    """
    return _write_build(tmp_path)


@pytest.fixture
def signed_build(tmp_path, signer) -> Path:
    """A good build whose SBOM is signed with *signer* (a verifiable signature).

    Verifies with the matching ``--pub`` key -> **PASS**. The public key file is
    written alongside (see :func:`pub`).
    """
    return _write_build(tmp_path, signed=True, signer=signer)


@pytest.fixture
def tampered_build(tmp_path, signer) -> Path:
    """A good build whose SBOM is corrupted *after* signing.

    The signature no longer matches the tampered body -> **FAIL** when verified
    with the matching ``--pub`` key.
    """
    return _write_build(tmp_path, signed=True, tamper=True, signer=signer)


@pytest.fixture
def empty_build(tmp_path) -> Path:
    """An empty build dir: no artifacts at all -> every kind missing -> **FAIL**.

    ``tmp_path`` is already an empty directory; nothing is written into it.
    """
    return tmp_path


@pytest.fixture
def partial_build(tmp_path) -> Path:
    """An incomplete build dir: only the SBOM and its signature present.

    The cycloneDX and repro report are missing -> not witness-ready -> **FAIL**.
    (The signature is a dummy here; this fixture is about *presence*, not
    verification.)
    """
    images = tmp_path / "images"
    images.mkdir(parents=True)
    (images / f"{_GOOD_TARGET}.spdx.json").write_text("SBOM")
    (images / f"{_GOOD_TARGET}.spdx.json.sig").write_text("SIG")
    return tmp_path


@pytest.fixture
def pub(tmp_path, signer) -> Path:
    """The ``signer``'s public key written to a ``pub.key`` file (for --pub)."""
    key_path = tmp_path / "pub.key"
    key_path.write_bytes(signer.pub)
    return key_path
