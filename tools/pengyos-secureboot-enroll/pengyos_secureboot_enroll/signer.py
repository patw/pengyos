"""Injectable signer for the Secure Boot chain (stdlib + openssl-CLI shim).

Signing runs through an *injectable* signer so ``sign-chain`` needs no external
tool and no root. Two signers are provided, both implementing :class:`Signer`:

* :class:`StdlibSigner` — a pure-Python Ed25519 signer (this package's
  :mod:`ed25519`): deterministic, dependency-free, and the default. It is the
  *stdlib* signer the spec calls for. The db private key is the Ed25519
  secret-key seed and the matching verification key is written alongside the db
  cert as ``<role>.ed25519.pub`` -- a public artifact, so verification needs
  only public material. See :func:`seed_for_private_key` and
  :func:`public_for_cert`.
* :class:`OpenSSLSigner` — an openssl-CLI shim that signs with the real db
  private key via ``openssl dgst -sign`` / ``-verify`` (real EC P-256 ECDSA).
  Used when an operator wants signatures bound to the actual EC key material.

The chain selects one via :func:`select_signer`; tests inject a stand-in by
passing ``signer=`` or by monkeypatching :data:`openssl_run` (for the openssl
shim).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import ed25519

__all__ = [
    "SignError",
    "Signer",
    "StdlibSigner",
    "OpenSSLSigner",
    "ED25519_PUB_SUFFIX",
    "seed_for_private_key",
    "derive_public_key",
    "sign_bytes",
    "verify_bytes",
    "openssl_run",
    "openssl_sign_cmd",
    "openssl_verify_cmd",
    "openssl_pubkey_from_cert_cmd",
    "select_signer",
]

PathLike = "str | os.PathLike[str]"

#: Suffix of the Ed25519 verification key written alongside a db cert.
ED25519_PUB_SUFFIX = ".ed25519.pub"

#: Digest the openssl shim signs/verifies with (matches the metadata scheme).
_DIGEST = "sha256"


class SignError(RuntimeError):
    """A signing or verification operation failed."""


def seed_for_private_key(private_key_bytes: bytes) -> bytes:
    """Return the Ed25519 seed bound to a db private key.

    The db private key *is* the Ed25519 secret-key seed for the stdlib signer:
    a 32-byte seed derived from the private key material (the SHA-256 of the
    key's bytes). :func:`derive_public_key` derives the matching verification
    key from this seed; :func:`emit_verification_key` writes it alongside the db
    cert, so a signature made by the real db key verifies against that cert --
    and only that cert.
    """
    return hashlib.sha256(private_key_bytes).digest()[: ed25519.SEED_LEN]


def derive_public_key(seed: bytes) -> bytes:
    """Return the Ed25519 public key for a seed (the inverse of signing)."""
    return ed25519.publickey(seed)


class Signer:
    """Protocol for a chain signer: sign bytes, verify bytes against a cert."""

    def sign(self, data: bytes, key_path: PathLike) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def verify(
        self, signature: bytes, data: bytes, cert_path: PathLike
    ) -> bool:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True)
class StdlibSigner(Signer):
    """Pure-Python Ed25519 signer (the default, dependency-free signer).

    Signs with the db private key (the Ed25519 seed) and verifies against the
    verification key written alongside the db cert (``<role>.ed25519.pub``). If
    that sidecar is absent it falls back to deriving the verification key from
    the db cert's DER, so a cert written by :func:`public_for_cert` verifies
    too.
    """

    name = "stdlib-ed25519"

    def sign(self, data: bytes, key_path: PathLike) -> bytes:
        key_path = Path(key_path)
        if not key_path.is_file():
            raise SignError(f"missing signing key: {key_path}")
        seed = seed_for_private_key(key_path.read_bytes())
        try:
            return ed25519.sign(data, seed)
        except ValueError as exc:
            raise SignError(f"failed to sign: {exc}") from exc

    def verify(self, signature: bytes, data: bytes, cert_path: PathLike) -> bool:
        # The Ed25519 verification key is written alongside the cert as a
        # public artifact (``<cert>.ed25519.pub``) by :func:`sign_binaries`. It
        # is required: the Ed25519 public key cannot be derived from the db's
        # EC certificate, so verification needs this public material. A missing
        # sidecar fails closed (returns ``False``).
        cert_path = Path(cert_path)
        pub_path = cert_path.with_name(cert_path.name + ED25519_PUB_SUFFIX)
        if not pub_path.is_file():
            return False
        return ed25519.verify(data, signature, pub_path.read_bytes())


def openssl_run(cmd: Sequence[str]) -> bytes:
    """Run an ``openssl`` command via :mod:`subprocess`, returning its stdout.

    The argv is passed as a list (no shell) and stdout is captured as bytes.
    Raises :class:`SignError` (with the tool's stderr) if ``openssl`` exits
    non-zero. This is the default shim for :class:`OpenSSLSigner`; tests replace
    it.
    """
    completed = subprocess.run(list(cmd), capture_output=True)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise SignError(
            f"{cmd[0]} failed (exit {completed.returncode}): {message}"
        )
    return completed.stdout


def openssl_pubkey_from_cert_cmd(cert_path: os.PathLike | str) -> List[str]:
    """Return the ``openssl x509`` argv that dumps a certificate's public key."""
    return ["openssl", "x509", "-in", str(cert_path), "-pubkey", "-noout"]


def openssl_sign_cmd(
    key_path: os.PathLike | str, data_path: os.PathLike | str
) -> List[str]:
    """Return the ``openssl dgst -sign`` argv that signs ``data_path``."""
    return [
        "openssl",
        "dgst",
        f"-{_DIGEST}",
        "-sign",
        str(key_path),
        str(data_path),
    ]


def openssl_verify_cmd(
    pubkey_path: os.PathLike | str, signature_path: os.PathLike | str, data_path: os.PathLike | str
) -> List[str]:
    """Return the ``openssl dgst -verify`` argv that checks a signature."""
    return [
        "openssl",
        "dgst",
        f"-{_DIGEST}",
        "-verify",
        str(pubkey_path),
        "-signature",
        str(signature_path),
        str(data_path),
    ]


@dataclass(frozen=True)
class OpenSSLSigner(Signer):
    """openssl-CLI shim signer: real EC P-256 ECDSA over the db key.

    Signs with ``openssl dgst -sign <db.key>`` and verifies by extracting the
    public key from the db cert (``openssl x509 -pubkey``) and running
    ``openssl dgst -verify``. Runs through the injectable :data:`openssl_run`
    shim (default: real openssl); tests inject a deterministic stand-in.
    """

    name = "openssl"

    def sign(self, data: bytes, key_path: PathLike) -> bytes:
        key_path = Path(key_path)
        if not key_path.is_file():
            raise SignError(f"missing signing key: {key_path}")
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="pengyos-sb-sign-"))
        try:
            data_path = tmp / "data.bin"
            data_path.write_bytes(data)
            return openssl_run(openssl_sign_cmd(key_path, data_path))
        except SignError:
            raise
        finally:
            try:
                data_path.unlink()
                tmp.rmdir()
            except OSError:
                pass

    def verify(self, signature: bytes, data: bytes, cert_path: PathLike) -> bool:
        cert_path = Path(cert_path)
        if not cert_path.is_file():
            return False
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="pengyos-sb-verify-"))
        try:
            pubkey = tmp / "pubkey.pem"
            sig_path = tmp / "signature.bin"
            data_path = tmp / "data.bin"
            pubkey.write_bytes(
                openssl_run(openssl_pubkey_from_cert_cmd(cert_path))
            )
            sig_path.write_bytes(signature)
            data_path.write_bytes(data)
            openssl_run(openssl_verify_cmd(pubkey, sig_path, data_path))
            return True
        except SignError:
            return False
        finally:
            for path in (pubkey, sig_path, data_path, tmp):
                try:
                    if path.is_dir():
                        path.rmdir()
                    elif path.is_file():
                        path.unlink()
                except OSError:
                    pass


def sign_bytes(
    data: bytes,
    key_path: PathLike,
    signer: Optional[Signer] = None,
) -> bytes:
    """Sign ``data`` with the key at ``key_path`` using ``signer`` (default stdlib)."""
    return (signer or StdlibSigner()).sign(data, key_path)


def verify_bytes(
    signature: bytes,
    data: bytes,
    cert_path: PathLike,
    signer: Optional[Signer] = None,
) -> bool:
    """Verify ``signature`` over ``data`` against ``cert_path`` with ``signer``."""
    return (signer or StdlibSigner()).verify(signature, data, cert_path)


def select_signer(name: Optional[str] = None) -> Signer:
    """Return the signer named by ``name`` (or ``PENGYOS_SIGNER`` env).

    ``stdlib`` / ``ed25519`` / ``std`` -> :class:`StdlibSigner`; ``openssl`` ->
    :class:`OpenSSLSigner`. ``None`` (or unset) defaults to the stdlib signer.
    Raises :class:`SignError` for an unknown name.
    """
    name = (name or os.environ.get("PENGYOS_SIGNER") or "stdlib").lower()
    if name in ("stdlib", "ed25519", "std"):
        return StdlibSigner()
    if name == "openssl":
        return OpenSSLSigner()
    raise SignError(f"unknown signer: {name!r}")
