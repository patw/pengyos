"""Sign the UKI + systemd-boot chain and verify (the ``sign-chain`` subtask).

This module signs each EFI binary, records the signature binding (sha256 +
keyid), and verifies it.

It does four things:

1. **Build the manifest** of EFI binaries to sign: the pinned PengyOS ESP
   boot-chain layout (:mod:`pengyos_secureboot_enroll.bootchain`), parameterised
   by edition/version/slots -- or an explicit list of ESP-relative paths.
2. **Sign each binary** present in the ESP (``--esp/<path>``) with the ``db``
   key, and record a :class:`SignatureBinding` -- the binary's SHA-256, the
   signing key id, and the signature.
3. **Verify every signature** against the ``db`` certificate.
4. **Persist the bindings** to ``signatures.json`` and emit the Ed25519
   verification-key sidecar alongside the ``db`` cert, so ``lint`` can check
   them.

Signing runs through the injectable signer
(:mod:`pengyos_secureboot_enroll.signer`); no root, no real NVRAM.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import signer as signer_module
from .signer import Signer, sign_bytes, verify_bytes
from .truststate import EspBinary, EspManifest
from .keys import TrustRoot
from . import bootchain

__all__ = [
    "SignChainError",
    "SignatureBinding",
    "SignedChain",
    "ED25519_PUB_SUFFIX",
    "sha256_bytes",
    "sign_path",
    "verify_binding",
    "sign_binaries",
    "emit_verification_key",
    "save_bindings",
    "load_bindings",
    "verify_bindings",
    "build_manifest",
]

PathLike = "str | os.PathLike[str]"

#: Suffix of the Ed25519 verification key emitted alongside the db cert.
ED25519_PUB_SUFFIX = ".ed25519.pub"

#: Default edition/version/slots used when ``sign-chain`` is given no override.
DEFAULT_EDITION = "server"
DEFAULT_VERSION = "1.0.0"
DEFAULT_SLOTS = ["a"]

#: Filename the signed bindings are persisted to (next to the key material).
SIGNATURES_JSON = "signatures.json"


class SignChainError(RuntimeError):
    """A signing or verification operation failed (fail closed)."""


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SignatureBinding:
    """A recorded signature over one EFI binary on the ESP.

    ``path`` is the ESP-relative path of the signed binary; ``sha256`` is the
    digest of the (unsigned) binary bytes; ``key_id`` is the signing key id
    (the ``db`` fingerprint); ``signature`` is the hex-encoded signature;
    ``signer`` is the signer name (``stdlib-ed25519`` / ``openssl``).
    """

    path: str
    sha256: str
    key_id: str
    signature: str
    signer: str

    def to_json(self) -> dict:
        """Return a JSON-serialisable representation of the binding."""
        return {
            "path": self.path,
            "sha256": self.sha256,
            "key_id": self.key_id,
            "signature": self.signature,
            "signer": self.signer,
        }

    @classmethod
    def from_json(cls, data: dict) -> "SignatureBinding":
        """Reconstruct a binding from its :meth:`to_json` form."""
        return cls(
            path=data["path"],
            sha256=data["sha256"],
            key_id=data["key_id"],
            signature=data["signature"],
            signer=data["signer"],
        )


@dataclass
class SignedChain:
    """The result of signing + verifying the chain: bindings + verify status."""

    bindings: List[SignatureBinding] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """``True`` when every binding is present (all verified at sign time)."""
        return bool(self.bindings)

    def to_json(self) -> dict:
        """Return a JSON-serialisable representation of the signed chain."""
        return {
            "signer": self.bindings[0].signer if self.bindings else "",
            "count": len(self.bindings),
            "verified": self.verified,
            "bindings": [b.to_json() for b in self.bindings],
        }


def _resolve_esp_path(esp_dir: Path, binary: EspBinary) -> Path:
    """Return the on-disk path of ``binary`` under ``esp_dir``.

    Raises :class:`SignChainError` when the binary is not present (fail closed
    -- an expected binary that cannot be signed cannot be part of the chain).
    """
    path = esp_dir / binary.path.lstrip("/")
    if not path.is_file():
        raise SignChainError(
            f"expected EFI binary not found in ESP: {binary.path} "
            f"(looked under {esp_dir})"
        )
    return path


def sign_path(
    data: bytes,
    key_path: Path,
    trust_root: TrustRoot,
    signer: Optional[Signer] = None,
) -> bytes:
    """Sign ``data`` with the ``db`` key and return the raw signature bytes."""
    return sign_bytes(data, key_path, signer=signer)


def verify_binding(
    binding: SignatureBinding,
    data: bytes,
    cert_path: Path,
    signer: Optional[Signer] = None,
) -> bool:
    """Return ``True`` when ``binding`` verifies over ``data`` against ``cert_path``."""
    return verify_bytes(
        bytes.fromhex(binding.signature), data, cert_path, signer=signer
    )


def emit_verification_key(
    cert_path: Path, signer: Optional[Signer] = None
) -> Path:
    """Emit the Ed25519 verification-key sidecar alongside ``cert_path``.

    Writes ``<cert>.ed25519.pub`` (world-readable) so :class:`StdlibSigner`
    can verify the db key's signatures against public material alone. Returns
    the path written.
    """
    cert_path = Path(cert_path)
    from .signer import StdlibSigner, derive_public_key, seed_for_private_key

    if not isinstance(signer, signer_module.StdlibSigner):
        # Only the stdlib signer needs the Ed25519 sidecar.
        return cert_path.with_name(cert_path.name + ED25519_PUB_SUFFIX)
    key_path = cert_path.with_suffix(".key")
    if not key_path.is_file():
        raise SignChainError(
            f"missing db private key {key_path}; cannot emit verification key"
        )
    pub_path = cert_path.with_name(cert_path.name + ED25519_PUB_SUFFIX)
    pub = signer_module.derive_public_key(
        signer_module.seed_for_private_key(key_path.read_bytes())
    )
    pub_path.write_bytes(pub)
    pub_path.chmod(0o644)
    return pub_path


def sign_binaries(
    esp_dir: PathLike,
    manifest: EspManifest,
    trust_root: TrustRoot,
    key_path: Optional[Path] = None,
    cert_path: Optional[Path] = None,
    signer: Optional[Signer] = None,
) -> SignedChain:
    """Sign every binary in ``manifest`` present under ``esp_dir`` and verify.

    For each binary the unsigned bytes are read, SHA-256'd, signed with the
    ``db`` key, and a :class:`SignatureBinding` is recorded. Each binding is
    then verified against the ``db`` certificate. Any failure raises
    :class:`SignChainError` (fail closed). The Ed25519 verification key is
    emitted alongside the db cert.

    ``key_path`` / ``cert_path`` default to ``<role>.key`` / ``<role>.cert`` in
    the db key's directory (inferred from ``trust_root.db``). ``signer`` defaults
    to the stdlib signer.
    """
    signer = signer or signer_module.StdlibSigner()
    esp_dir = Path(esp_dir)
    db = trust_root.db

    if key_path is None:
        key_path = Path(db.key_path)
    if cert_path is None:
        cert_path = Path(db.cert_path)

    if not key_path.is_file():
        raise SignChainError(f"missing db signing key: {key_path}")
    if not cert_path.is_file():
        raise SignChainError(f"missing db certificate: {cert_path}")

    # Emit the Ed25519 verification key for the stdlib signer FIRST, so the
    # per-binary verification below can resolve it against public material.
    emit_verification_key(cert_path, signer)

    bindings: List[SignatureBinding] = []
    for binary in manifest:
        data = _resolve_esp_path(esp_dir, binary).read_bytes()
        digest = sha256_bytes(data)
        signature = sign_bytes(data, key_path, signer=signer)
        if not verify_bytes(signature, data, cert_path, signer=signer):
            raise SignChainError(
                f"signature over {binary.path} failed to verify (fail closed)"
            )
        bindings.append(
            SignatureBinding(
                path=binary.path,
                sha256=digest,
                key_id=db.key_id,
                signature=signature.hex(),
                signer=signer.name,
            )
        )

    return SignedChain(bindings=bindings)


def save_bindings(
    bindings: Sequence[SignatureBinding], out_path: PathLike
) -> Path:
    """Persist ``bindings`` as JSON (0644) next to the key material."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "bindings": [b.to_json() for b in bindings],
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_path.chmod(0o644)
    return out_path


def load_bindings(out_path: PathLike) -> List[SignatureBinding]:
    """Load recorded bindings from ``out_path`` (as written by ``save_bindings``)."""
    out_path = Path(out_path)
    if not out_path.is_file():
        return []
    data = json.loads(out_path.read_text(encoding="utf-8"))
    return [SignatureBinding.from_json(b) for b in data.get("bindings", [])]


def verify_bindings(
    bindings: Sequence[SignatureBinding],
    esp_dir: PathLike,
    cert_path: Path,
    signer: Optional[Signer] = None,
) -> bool:
    """Verify every recorded binding against the db cert and the ESP bytes.

    Returns ``True`` only when every binding verifies over the current ESP
    binary bytes. A missing binary or a failed verification returns ``False``
    (fail closed).
    """
    signer = signer or signer_module.StdlibSigner()
    esp_dir = Path(esp_dir)
    for binding in bindings:
        data_path = esp_dir / binding.path.lstrip("/")
        if not data_path.is_file():
            return False
        if not verify_binding(binding, data_path.read_bytes(), cert_path, signer):
            return False
    return True


def build_manifest(
    edition: str = DEFAULT_EDITION,
    version: str = DEFAULT_VERSION,
    slots: Optional[Sequence[str]] = None,
    paths: Optional[Sequence[str]] = None,
) -> EspManifest:
    """Build the manifest to sign.

    When ``paths`` is given it is used verbatim (an explicit, caller-owned set
    of ESP-relative paths); otherwise the pinned PengyOS boot-chain layout
    (:func:`pengyos_secureboot_enroll.bootchain.expected_manifest`) is used with
    ``edition``/``version``/``slots`` (defaulting to a single ``a`` slot).
    """
    if paths:
        return EspManifest(
            binaries=[EspBinary(path=p) for p in paths]
        )
    return bootchain.expected_manifest(edition, version, list(slots or DEFAULT_SLOTS))
