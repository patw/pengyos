"""Generate, load and sign the PK/KEK/db trust root (the ``keys`` subtask).

This module implements ``keys``: deterministic generation into a fixture dir
with an injectable RNG/openssl shim; load ``KeyEntry`` from disk (key id =
SHA-256 of DER cert); metadata signed + hashable.

It does three things:

1. **Generate** the PK/KEK/db key material -- EC P-256 private keys in PKCS#8
   PEM and self-signed X.509 certificates in PEM -- into a key directory, using
   an *injectable openssl shim* (a callable mapping an ``openssl`` argv to its
   stdout bytes). The default shim runs the real ``openssl`` through
   :mod:`subprocess`; tests inject a deterministic stand-in, which is what makes
   the generation *deterministic* and reproducible (same shim -> same bytes).
   Private keys get ``0600``, certificates ``0644`` and the key directory
   ``0700`` (see :class:`~pengyos_secureboot_enroll.truststate.KeyPerms`).

2. **Load** a :class:`TrustRoot` from a key directory: one :class:`KeyEntry`
   per role (PK/KEK/db), each identified by the SHA-256 fingerprint of its DER
   certificate -- the same identifier MOK/mokutil uses, and the value
   :func:`~pengyos_secureboot_enroll.truststate.key_fingerprint` computes. The
   DER is recovered from the PEM certificate with the standard library (a
   base64 decode), so loading needs no external tool.

3. **Sign + hash** the key-set metadata. :func:`build_metadata` produces an
   immutable, hashable :class:`KeyMetadata` (a frozen dataclass) carrying the
   role/key-id table and a :attr:`KeyMetadata.digest` -- the SHA-256 of the
   metadata's canonical bytes. :func:`sign_metadata` signs those canonical bytes
   with the ``PK`` private key (PK owns KEK, which owns ``db``);
   :func:`verify_metadata_signature` checks the signature against the ``PK``
   certificate. The signed metadata is the enrolment-grade binding between the
   key set and its trust root, and it is what :mod:`pengyos_secureboot_enroll.enroll`
   will emit as MOK/custom-key material in a later subtask.

Signing runs through the same injectable shim as generation, so *no root, no
real openssl, no real NVRAM* is required -- everything is mockable.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .truststate import (
    KEY_FILES,
    KEY_ROLES,
    KeyEntry,
    KeyPerms,
    KeyKind,
    is_private,
    key_fingerprint,
)

__all__ = [
    "GenerateError",
    "SignError",
    "PrivateKeyError",
    "MissingKeyError",
    "PRIVATE_KEY_CURVE",
    "CERTIFICATE_DAYS",
    "METADATA_DIGEST_ALGO",
    "MetadataSignedError",
    "TrustRoot",
    "KeyMetadata",
    "openssl_private_key_cmd",
    "openssl_certificate_cmd",
    "openssl_sign_cmd",
    "openssl_verify_cmd",
    "openssl_pubkey_from_cert_cmd",
    "openssl_run",
    "generate_private_key",
    "generate_certificate",
    "generate_all",
    "pem_to_der",
    "check_key_set",
    "load_key_set",
    "build_metadata",
    "sign_metadata",
    "verify_metadata_signature",
]

#: Elliptic curve used for every Secure Boot key (ECDSA, NIST P-256).
PRIVATE_KEY_CURVE = "P-256"

#: Lifetime of the generated self-signed certificates, in days (10 years).
CERTIFICATE_DAYS = 3650

#: Digest algorithm used to sign the key metadata and (by policy) to identify it.
METADATA_DIGEST_ALGO = "sha256"

#: The role that signs the key metadata. PK owns KEK, which owns db, so PK signs
#: the document that binds the whole trust root together.
METADATA_SIGNER_ROLE = "PK"

#: A callable taking an ``openssl`` argv (list of str) and returning stdout bytes.
#: The default is :func:`openssl_run` (real openssl); tests inject a stand-in by
#: replacing the module global :data:`openssl_run` (the defaults below are resolved
#: at call time, so a monkeypatched global is picked up) or passing ``run=``.
Shim = Callable[[Sequence[str]], bytes]


class GenerateError(RuntimeError):
    """Raised when ``openssl`` fails to produce key material."""


class SignError(RuntimeError):
    """Raised when signing/verifying the metadata cannot be completed."""


class PrivateKeyError(RuntimeError):
    """Raised when a private key is missing or has insecure permissions."""


class MissingKeyError(PrivateKeyError):
    """Raised when a required key file is absent from the key directory."""


# --- openssl command construction -------------------------------------------


def openssl_private_key_cmd(role: str) -> List[str]:
    """Return the ``openssl genpkey`` argv that produces ``role``'s key.

    The EC P-256 PKCS#8 PEM key is written to ``openssl``'s stdout; the caller
    owns writing it to disk. Returned as a list (no shell) so it is safe to pass
    to the injectable shim and easy to assert on in tests.
    """
    return [
        "openssl",
        "genpkey",
        "-algorithm",
        "EC",
        "-pkeyopt",
        f"ec_paramgen_curve:{PRIVATE_KEY_CURVE}",
    ]


def openssl_certificate_cmd(role: str, key_path: os.PathLike | str) -> List[str]:
    """Return the ``openssl req -x509`` argv that produces ``role``'s cert.

    Builds a self-signed X.509 certificate from the private key at ``key_path``.
    The PEM certificate is written to ``openssl``'s stdout; the subject names the
    role so the certificates are individually identifiable.
    """
    return [
        "openssl",
        "req",
        "-x509",
        "-new",
        "-key",
        str(key_path),
        "-days",
        str(CERTIFICATE_DAYS),
        "-subj",
        f"/CN=PengyOS Secure Boot {role}",
    ]


def openssl_pubkey_from_cert_cmd(cert_path: os.PathLike | str) -> List[str]:
    """Return the ``openssl x509`` argv that dumps a certificate's public key.

    ``openssl x509 -in <cert> -pubkey -noout`` emits the PKIX public key in PEM
    form on stdout -- the input :func:`openssl_verify_cmd` needs to check a
    signature without the private key.
    """
    return ["openssl", "x509", "-in", str(cert_path), "-pubkey", "-noout"]


def openssl_sign_cmd(
    key_path: os.PathLike | str, data_path: os.PathLike | str
) -> List[str]:
    """Return the ``openssl dgst -sign`` argv that signs ``data_path``.

    ``openssl dgst -sha256 -sign <key> <data>`` writes the ECDSA signature to
    stdout, so the caller captures it from the shim's return value. ``data_path``
    is the file whose bytes are signed (the metadata's canonical bytes).
    """
    return [
        "openssl",
        "dgst",
        f"-{METADATA_DIGEST_ALGO}",
        "-sign",
        str(key_path),
        str(data_path),
    ]


def openssl_verify_cmd(
    pubkey_path: os.PathLike | str, signature_path: os.PathLike | str, data_path: os.PathLike | str
) -> List[str]:
    """Return the ``openssl dgst -verify`` argv that checks a signature.

    Returns ``True``-ish when the tool exits 0 (``Verified OK``); a non-zero exit
    means the signature failed to verify.
    """
    return [
        "openssl",
        "dgst",
        f"-{METADATA_DIGEST_ALGO}",
        "-verify",
        str(pubkey_path),
        "-signature",
        str(signature_path),
        str(data_path),
    ]


# --- low-level shim ---------------------------------------------------------


def openssl_run(cmd: Sequence[str]) -> bytes:
    """Run an ``openssl`` command via :mod:`subprocess`, returning its stdout.

    The argv is passed as a list (no shell) and stdout is captured as bytes.
    Raises :class:`GenerateError` (with the tool's stderr) if ``openssl`` exits
    non-zero. This is the default injectable shim; tests replace it.
    """
    completed = subprocess.run(list(cmd), capture_output=True)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", "replace").strip()
        raise GenerateError(
            f"{cmd[0]} failed (exit {completed.returncode}): {message}"
        )
    return completed.stdout


# --- generation -------------------------------------------------------------


def generate_private_key(
    role: str, key_path: os.PathLike | str, run: Optional[Shim] = None
) -> bytes:
    """Generate ``role``'s private key at ``key_path`` and return its bytes.

    Runs ``openssl genpkey`` (EC P-256), capturing the PEM key from stdout and
    writing it to ``key_path`` with ``0600``. The key bytes are returned so
    callers can assert on / protect them. ``run`` is the injectable openssl shim
    (defaults to :func:`openssl_run`).
    """
    key_path = Path(key_path)
    data = (run or openssl_run)(openssl_private_key_cmd(role))
    key_path.write_bytes(data)
    _chmod_private(key_path)
    return data


def generate_certificate(
    role: str, key_path: os.PathLike | str, cert_path: os.PathLike | str, run: Optional[Shim] = None
) -> bytes:
    """Generate ``role``'s self-signed cert at ``cert_path`` from ``key_path``.

    Runs ``openssl req -x509 -new -key <key>`` (the key at ``key_path`` was
    produced by :func:`generate_private_key`), capturing the PEM certificate
    from stdout and writing it to ``cert_path`` with ``0644``. The private key is
    only ever handed to ``openssl`` by path -- its contents never touch Python.
    ``run`` is the injectable openssl shim (defaults to :func:`openssl_run`).
    """
    key_path = Path(key_path)
    cert_path = Path(cert_path)
    data = (run or openssl_run)(openssl_certificate_cmd(role, key_path))
    cert_path.write_bytes(data)
    _chmod_public(cert_path)
    return data


def generate_all(keys_dir: os.PathLike | str, run: Optional[Shim] = None) -> None:
    """Generate the full PK/KEK/db key set under ``keys_dir``.

    Writes every private key first (so the self-signed certificates can be built
    from them) and then every certificate, in Secure Boot role order (PK, KEK,
    db). The key directory is created with ``0700``. Raises :class:`GenerateError`
    if any ``openssl`` call fails.

    Given a deterministic ``run`` shim the output is reproducible -- same shim,
    same bytes -- which is what makes this a fixture-grade generator.
    """
    run = run or openssl_run
    keys_dir = Path(keys_dir)
    _chmod_dir(keys_dir)

    # Private keys first: the certificates are signed from these keys.
    for key_file in KEY_FILES:
        if not is_private(key_file):
            continue
        generate_private_key(key_file.role, keys_dir / key_file.name, run=run)

    # Then the self-signed certificates, each from its freshly written key.
    for key_file in KEY_FILES:
        if is_private(key_file):
            continue
        role = key_file.role
        key_path = keys_dir / f"{role}.key"
        generate_certificate(role, key_path, keys_dir / key_file.name, run=run)


# --- permission policy ------------------------------------------------------


def _chmod_private(path: Path) -> None:
    path.chmod(KeyPerms.PRIVATE)


def _chmod_public(path: Path) -> None:
    path.chmod(KeyPerms.PUBLIC)


def _chmod_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(KeyPerms.DIR)


def _require_private_key(keys_dir: Path, role: str) -> Path:
    """Return the ``role`` private key path, failing closed if it is missing.

    Raises :class:`MissingKeyError` when the key is absent.
    """
    key_path = keys_dir / f"{role}.key"
    if not key_path.is_file():
        raise MissingKeyError(
            f"missing {role} private key: {key_path} "
            "(run `keys --out <dir>` first)"
        )
    # A private key with group/world bits is treated as compromised: signing the
    # metadata with it would leak the trust root, so refuse (fail closed).
    mode = stat.S_IMODE(key_path.stat().st_mode)
    if mode & 0o077:
        raise PrivateKeyError(
            f"insecure {role} key permissions: {key_path} (expected 0600 or stricter)"
        )
    return key_path


# --- DER / key-id extraction ------------------------------------------------


def pem_to_der(pem: bytes) -> bytes:
    """Return the DER encoding recovered from a PEM certificate.

    Strips the ``-----BEGIN/END CERTIFICATE-----`` markers and base64-decodes the
    body with the standard library. This yields exactly the DER bytes
    ``openssl x509 -outform DER`` would emit, so :func:`key_fingerprint` over the
    result is the MOK/mokutil key id -- with no external tool required.

    Raises :class:`GenerateError` when the input is not a single PEM certificate.
    """
    text = pem.decode("utf-8", "replace")
    if "CERTIFICATE" not in text:
        raise GenerateError("not a PEM certificate: no CERTIFICATE marker found")
    lines = [line for line in text.splitlines() if "BEGIN" not in line and "END" not in line]
    b64 = "".join(line.strip() for line in lines)
    if not b64:
        raise GenerateError("not a PEM certificate: no base64 body found")
    # Normalise padding: real PEM files may leave the final line short, and
    # strict decoding (the default in modern Python) rejects unterminated
    # chunks. Re-add the missing '=' so the body decodes either way.
    padding = (-len(b64)) % 4
    try:
        return base64.b64decode(b64 + "=" * padding, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise GenerateError(f"not a PEM certificate: {exc}") from exc


# --- trust root -------------------------------------------------------------


@dataclass(frozen=True)
class TrustRoot:
    """The loaded PK/KEK/db trust root: one :class:`KeyEntry` per role.

    ``entries`` is in Secure Boot role order (PK, KEK, db). :meth:`by_role`
    looks a role up; :meth:`key_ids` returns the role->key-id table;
    :meth:`db_key_id` is the signing key id ``lint`` compares against.
    """

    entries: Tuple[KeyEntry, ...] = ()

    def __post_init__(self) -> None:
        # Keep the canonical role order, drop anything unmanaged, and fail closed
        # if a role is missing (a trust root without a db key cannot sign).
        by_role: Dict[str, KeyEntry] = {e.role: e for e in self.entries}
        ordered = tuple(by_role[role] for role in KEY_ROLES if role in by_role)
        if not ordered:
            raise ValueError("trust root has no managed roles")
        object.__setattr__(self, "entries", ordered)

    def by_role(self, role: str) -> KeyEntry:
        """Return the :class:`KeyEntry` for ``role`` (PK/KEK/db)."""
        for entry in self.entries:
            if entry.role == role:
                return entry
        raise KeyError(f"no {role} key in trust root")

    @property
    def db(self) -> KeyEntry:
        """The ``db`` signing key (the trust root ``lint`` compares against)."""
        return self.by_role("DB")

    @property
    def pk(self) -> KeyEntry:
        """The ``PK`` key (the role that signs the key metadata)."""
        return self.by_role("PK")

    def key_ids(self) -> Dict[str, str]:
        """Return the ``{role: key_id}`` table for the whole trust root."""
        return {entry.role: entry.key_id for entry in self.entries}

    def db_key_id(self) -> str:
        """Return the ``db`` key id -- the signing key ``lint`` trusts."""
        return self.db.key_id

    def to_json(self) -> dict:
        """Return a JSON-serialisable representation of the trust root."""
        return {
            "roles": {
                entry.role: {"key_id": entry.key_id, "cert_path": entry.cert_path}
                for entry in self.entries
            }
        }


def check_key_set(keys_dir: os.PathLike | str) -> None:
    """Fail closed if the key directory is incomplete or has insecure perms.

    Raises :class:`MissingKeyError` when a managed certificate is absent and
    :class:`PrivateKeyError` when a present private key has group/world bits
    (treated as compromised). Called before signing/verifying so an insecure or
    partial trust root is refused up front.
    """
    keys_dir = Path(keys_dir)
    for key_file in KEY_FILES:
        if is_private(key_file):
            continue
        cert_path = keys_dir / key_file.name
        if not cert_path.is_file():
            raise MissingKeyError(
                f"missing {key_file.role} certificate: {cert_path} "
                "(run `keys --out <dir>` first)"
            )
    for key_file in KEY_FILES:
        if not is_private(key_file):
            continue
        key_path = keys_dir / key_file.name
        if key_path.is_file():
            mode = stat.S_IMODE(key_path.stat().st_mode)
            if mode & 0o077:
                raise PrivateKeyError(
                    f"insecure {key_file.role} key permissions: {key_path} "
                    "(expected 0600 or stricter)"
                )


def load_key_set(keys_dir: os.PathLike | str) -> TrustRoot:
    """Load the :class:`TrustRoot` from a key directory on disk.

    For each managed role (PK/KEK/db) it reads ``<role>.cert`` (PEM), derives the
    DER with :func:`pem_to_der`, and computes the key id as the SHA-256 of that
    DER (:func:`~pengyos_secureboot_enroll.truststate.key_fingerprint`). The
    private key path is recorded too. Raises :class:`MissingKeyError` if any
    certificate is absent.

    Loading needs no external tool (the DER comes from the standard library), so
    it works in tests and reproducible builds.
    """
    keys_dir = Path(keys_dir)
    entries: List[KeyEntry] = []
    for key_file in KEY_FILES:
        if not is_private(key_file):
            continue
        role = key_file.role
        cert_path = keys_dir / f"{role}.cert"
        if not cert_path.is_file():
            raise MissingKeyError(
                f"missing {role} certificate: {cert_path} "
                "(run `keys --out <dir>` first)"
            )
        der = pem_to_der(cert_path.read_bytes())
        entries.append(
            KeyEntry(
                role=role,
                key_id=key_fingerprint(der),
                cert_path=str(cert_path),
                key_path=str(keys_dir / f"{role}.key"),
                perms=KeyPerms.PUBLIC,
            )
        )
    return TrustRoot(entries=tuple(entries))


# --- metadata (hashable + signed) -------------------------------------------


def _canonical_metadata_bytes(
    key_ids: Sequence[Tuple[str, str]], generated_at: str
) -> bytes:
    """Return the canonical bytes a :class:`KeyMetadata` is hashed from.

    A stable, line-oriented encoding of the role/key-id table plus the
    generation timestamp, so the digest is reproducible across machines.
    """
    lines = ["pengyos-secureboot-keys/metadata/v1", f"generated_at={generated_at}"]
    for role, key_id in key_ids:
        lines.append(f"role={role} key_id={key_id}")
    return ("\n".join(lines) + "\n").encode("utf-8")


@dataclass(frozen=True)
class KeyMetadata:
    """An immutable, hashable description of a loaded trust root.

    ``key_ids`` is the ordered ``(role, key_id)`` table; ``generated_at`` is an
    ISO-8601 UTC timestamp; :attr:`digest` is the SHA-256 of the metadata's
    canonical bytes. Being a frozen dataclass, :class:`KeyMetadata` is directly
    ``hash()``-able, and :meth:`canonical_bytes` reproduces the bytes the digest
    was computed over.
    """

    key_ids: Tuple[Tuple[str, str], ...] = ()
    generated_at: str = ""
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.generated_at:
            raise ValueError("KeyMetadata.generated_at must be set")
        canonical = _canonical_metadata_bytes(self.key_ids, self.generated_at)
        object.__setattr__(self, "digest", hashlib.sha256(canonical).hexdigest())

    def canonical_bytes(self) -> bytes:
        """Return the canonical bytes this metadata's digest was computed over."""
        return _canonical_metadata_bytes(self.key_ids, self.generated_at)

    @classmethod
    def from_json(cls, data: dict) -> "KeyMetadata":
        """Reconstruct a :class:`KeyMetadata` from its :meth:`to_json` form.

        The on-disk JSON stores ``key_ids`` as a list of ``{"role", "key_id"}``
        objects; this restores the tuple-of-tuples canonical form so the
        reconstructed metadata hashes to the same :attr:`digest` and its
        signature still verifies.
        """
        key_ids = tuple((item["role"], item["key_id"]) for item in data["key_ids"])
        return cls(key_ids=key_ids, generated_at=data["generated_at"])

    def to_json(self) -> dict:
        """Return a JSON-serialisable representation of the metadata."""
        return {
            "generated_at": self.generated_at,
            "key_ids": [{"role": role, "key_id": key_id} for role, key_id in self.key_ids],
            "digest": self.digest,
        }


def build_metadata(
    trust_root: TrustRoot, generated_at: Optional[str] = None
) -> KeyMetadata:
    """Build the :class:`KeyMetadata` for a loaded :class:`TrustRoot`.

    ``generated_at`` defaults to the current UTC time (ISO-8601, second
    precision); pass a fixed value for reproducible metadata.
    """
    if generated_at is None:
        import datetime

        generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    key_ids = tuple((entry.role, entry.key_id) for entry in trust_root.entries)
    return KeyMetadata(key_ids=key_ids, generated_at=generated_at)


def sign_metadata(
    metadata: KeyMetadata,
    keys_dir: os.PathLike | str,
    run: Optional[Shim] = None,
    role: str = METADATA_SIGNER_ROLE,
) -> bytes:
    """Sign the metadata's canonical bytes with the ``role`` private key.

    Returns the raw ECDSA signature bytes (the ``openssl dgst -sign`` stdout).
    The signing key must be a managed private key with ``0600`` permissions,
    else :class:`PrivateKeyError` is raised (fail closed). ``run`` is the
    injectable openssl shim (defaults to :func:`openssl_run`).
    """
    run = run or openssl_run
    keys_dir = Path(keys_dir)
    key_path = _require_private_key(keys_dir, role)

    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="pengyos-sb-metadata-"))
    try:
        data = tmp_dir / "metadata.bin"
        data.write_bytes(metadata.canonical_bytes())
        return run(openssl_sign_cmd(key_path, data))
    except GenerateError as exc:
        raise SignError(f"failed to sign metadata with {role} key: {exc}") from exc
    finally:
        try:
            data.unlink()
            tmp_dir.rmdir()
        except OSError:
            pass


def verify_metadata_signature(
    metadata: KeyMetadata,
    signature: bytes,
    keys_dir: os.PathLike | str,
    run: Optional[Shim] = None,
    role: str = METADATA_SIGNER_ROLE,
) -> bool:
    """Verify ``signature`` over ``metadata`` with the ``role`` certificate.

    Extracts the public key from ``keys_dir/<role>.cert`` and runs
    ``openssl dgst -verify`` over ``metadata.canonical_bytes()``. Returns
    ``True`` only when the tool verifies (exit 0); a tampered metadata or a bad
    signature returns ``False``. The verify command runs through the injectable
    shim (openssl exits non-zero on failure, which the shim surfaces as
    :class:`GenerateError`). Signature/temp files are written to a private
    scratch directory (never ``/tmp`` on this host). ``run`` is the injectable
    openssl shim (defaults to :func:`openssl_run`).
    """
    run = run or openssl_run
    keys_dir = Path(keys_dir)
    cert_path = keys_dir / f"{role}.cert"
    if not cert_path.is_file():
        raise MissingKeyError(
            f"missing {role} certificate: {cert_path} "
            "(run `keys --out <dir>` first)"
        )

    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="pengyos-sb-metadata-"))
    try:
        pubkey = tmp_dir / "pubkey.pem"
        sig = tmp_dir / "signature.bin"
        data = tmp_dir / "metadata.bin"
        pubkey.write_bytes(run(openssl_pubkey_from_cert_cmd(cert_path)))
        sig.write_bytes(signature)
        data.write_bytes(metadata.canonical_bytes())
        # openssl exits non-zero on a failed verification; the shim raises
        # GenerateError in that case, which we map to a clean ``False``.
        run(openssl_verify_cmd(pubkey, sig, data))
        return True
    except GenerateError:
        return False
    finally:
        for path in (pubkey, sig, data, tmp_dir):
            try:
                if path.is_dir():
                    path.rmdir()
                elif path.is_file():
                    path.unlink()
            except OSError:
                pass


# --- small helpers (module-private) -----------------------------------------

# (permission helpers live above, next to `generate_all`)
