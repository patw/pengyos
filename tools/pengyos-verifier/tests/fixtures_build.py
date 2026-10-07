"""Shared fixture builders for good/bad build output dirs.

Everything is offline and root-free: a "good" build dir is assembled
from fixed byte strings plus in-tree format modules (the witness never
trusts producer tools), so the same dir verifies identically on every
run and machine. Individual tests mutate a copy of the good dir to
create bad variants.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional, Tuple

from pengyos_verifier import checks, ed25519, signature_format

#: Fixed, deterministic artifact contents (fake-but-nonempty, and
#: distinct so a cross-wired signature is always detectable).
ROOTFS_BYTES = b"pengyos-fake-rootfs-ext4 " + bytes(range(256))
BZIMAGE_BYTES = b"pengyos-fake-bzImage " + bytes(range(255, -1, -1))

#: keyid is SHA-256(public_key) per pengyos-sig-v1; this is the digest
#: of a fixed fake public key, not a real key.
FAKE_KEYID = hashlib.sha256(b"fake-pengyos-public-key").hexdigest()


def make_sig_bytes(artifact: bytes,
                   keyid: str = FAKE_KEYID,
                   body: bytes = b"\x00" * signature_format.BODY_BYTES) -> bytes:
    """A structurally valid v1 .sig for ``artifact`` (digest-correct).

    The default body is 64 zero bytes: structurally valid but NOT a
    real ed25519 signature — exactly what the key-independent
    signatures check verifies. For a build dir whose signatures
    actually verify, use :func:`build_signed_dir`.
    """
    digest = hashlib.sha256(artifact).hexdigest()
    header = f"pengyos-sig-v1 1 ed25519 {keyid} sha256 {digest}\n"
    return header.encode("ascii") + body


def make_real_sig_bytes(artifact: bytes, seed: bytes) -> bytes:
    """A v1 .sig whose body is a REAL ed25519 signature of ``artifact``.

    ``keyid`` is ``SHA-256(public_key)`` per pengyos-sig-v1, so the
    header binds the body to the public key derived from ``seed``. Per
    pengyos-sig-v1 (pengyos-sign FORMAT.md) the signature is over the
    artifact's SHA-256 *digest*, not its raw bytes.
    """
    pub = ed25519.publickey(seed)
    body = ed25519.sign(hashlib.sha256(artifact).digest(), seed)
    return make_sig_bytes(artifact, keyid=signature_format.keyid_of(pub),
                          body=body)


def build_signed_dir(root, seed: Optional[bytes] = None) -> Tuple[Dict[str, str], bytes]:
    """A fully good build dir whose ``.sig`` bodies genuinely verify.

    Returns ``(paths, public_key)`` where *paths* has the same shape as
    :func:`build_good_dir` and *public_key* is the 32-byte ed25519
    public key the signatures were made with (``seed`` defaults to a
    fixed deterministic one, so the output is reproducible).
    """
    seed = bytes(32) if seed is None else seed
    pub = ed25519.publickey(seed)
    root = str(root)
    os.makedirs(os.path.join(root, "images"), exist_ok=True)
    paths = {}
    for artifact, content in (
        (checks.EXPECTED_ARTIFACTS[0], ROOTFS_BYTES),
        (checks.EXPECTED_ARTIFACTS[1], BZIMAGE_BYTES),
    ):
        path = f"{root}/{artifact}"
        _write(path, content)
        paths[artifact] = path
        paths[artifact + checks.SIGNATURE_SUFFIX] = \
            f"{root}/{artifact + checks.SIGNATURE_SUFFIX}"
        _write(paths[artifact + checks.SIGNATURE_SUFFIX],
               make_real_sig_bytes(content, seed))
    paths["sbom"] = f"{root}/{checks.SBOM_FILENAMES[0]}"
    _write(paths["sbom"], GOOD_SPDX)
    paths["repro"] = f"{root}/{checks.REPRO_REPORT_FILENAME}"
    _write(paths["repro"], GOOD_REPRO)
    return paths, pub


GOOD_SPDX: Dict[str, Any] = {
    "spdxVersion": "SPDX-2.3",
    "dataLicense": "CC0-1.0",
    "SPDXID": "SPDXRef-DOCUMENT",
    "name": "pengyos-m1",
    "documentNamespace": "https://catbee.ca/pengyos/m1/sbom",
    "documentDescribes": ["SPDXRef-pengyos-m1"],
    "creationInfo": {
        "created": "2026-09-16T00:00:00Z",
        "creators": ["Tool: pengyos-sbom"],
    },
    "packages": [
        {
            "SPDXID": "SPDXRef-pengyos-m1",
            "name": "pengyos-m1",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
    ],
    "relationships": [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relatedSpdxElement": "SPDXRef-pengyos-m1",
            "relationshipType": "DESCRIBES",
        }
    ],
}

GOOD_CDX: Dict[str, Any] = {
    "bomFormat": "CycloneDX",
    "specVersion": "1.5",
    "serialNumber": "urn:uuid:12345678-1234-5678-1234-567812345678",
    "version": 1,
    "components": [
        {
            "type": "application",
            "name": "pengyos-m1",
            "bom-ref": "pengyos-m1",
            "purl": "pkg:generic/catbee/pengyos-m1@1.0",
        }
    ],
}

GOOD_REPRO: Dict[str, Any] = {"score": 1.0, "identical": True}


def _write(path, data) -> None:
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif isinstance(data, dict):
        data = (json.dumps(data, indent=2) + "\n").encode("utf-8")
    with open(path, "wb") as fh:
        fh.write(data)


def build_good_dir(root) -> Dict[str, str]:
    """Create a fully good build output dir under ``root``.

    Returns the absolute paths of the conventional files so tests can
    target specific ones.
    """
    root = str(root)
    os.makedirs(os.path.join(root, "images"), exist_ok=True)

    paths = {}
    for artifact, content in (
        (checks.EXPECTED_ARTIFACTS[0], ROOTFS_BYTES),   # images/rootfs.ext4
        (checks.EXPECTED_ARTIFACTS[1], BZIMAGE_BYTES),  # images/bzImage
    ):
        path = f"{root}/{artifact}"
        _write(path, content)
        paths[artifact] = path
        paths[artifact + checks.SIGNATURE_SUFFIX] = f"{root}/{artifact + checks.SIGNATURE_SUFFIX}"
        _write(paths[artifact + checks.SIGNATURE_SUFFIX],
               make_sig_bytes(content))

    paths["sbom"] = f"{root}/{checks.SBOM_FILENAMES[0]}"
    _write(paths["sbom"], GOOD_SPDX)
    paths["repro"] = f"{root}/{checks.REPRO_REPORT_FILENAME}"
    _write(paths["repro"], GOOD_REPRO)
    return paths


def make_ctx(tmp_path, attest_text: Optional[str] = "",
             threshold: float = checks.DEFAULT_REPRO_THRESHOLD):
    """A CheckContext over a good build dir in ``tmp_path``.

    The attest log is written with ``attest_text``; pass
    ``attest_text=None`` to not create the log file at all.
    """
    from pengyos_verifier.checks_impl import CheckContext
    build_dir = tmp_path / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    build_good_dir(build_dir)
    attest_path = tmp_path / "attest.log"
    if attest_text is not None:
        attest_path.write_text(attest_text)
    return CheckContext(build_dir=str(build_dir),
                        attest_path=str(attest_path),
                        repro_threshold=threshold)
