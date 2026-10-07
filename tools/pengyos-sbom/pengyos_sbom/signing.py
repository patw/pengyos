"""Detached ed25519 signing / verification of SBOM files.

This is the engine behind the ``sign`` and ``verify`` subcommands. It
wraps :mod:`pengyos_sbom.ed25519` with the file-level contract:

* **Key files** hold raw 32-byte keys. A *signing key* is the ed25519
  secret-key *seed*; a *verification key* is the 32-byte public key.
  A key file is either exactly 32 raw bytes, or a text file containing a
  single base64 string (padding optional, surrounding whitespace ignored).
  ``verify``'s key file may also contain a *seed*: verification tries both
  interpretations of the decoded 32 bytes — as a public key (when the bytes
  are a valid curve point) and as a seed (deriving the matching public key)
  — so the same file that signs can be used to verify. Both candidates are
  always tried: an ed25519 *seed* happens to be a valid curve point with
  ~50% probability, so the two interpretations are ambiguous and the only
  safe resolution is "accept iff either candidate verifies".
* **Canonicalization**: the signed byte string is
  ``canonical_bytes(raw file bytes)``. It normalizes the document's *semantic
  content* — for a JSON SPDX 2.3 / CycloneDX document the creation timestamp
  is fixed to a constant and the body is re-serialized canonically (sorted
  keys, compact separators, ASCII-escaped) — so a signature is stable across
  the creation timestamp and across whitespace / key-order differences, while
  any content change is still detected. Non-JSON bytes are bound verbatim.
  Because *both* ``sign`` and ``verify`` route through the same function, the
  round-trip invariant holds whatever this function returns.
* **Detached signatures**: a 64-byte binary file, written next to the SBOM
  as ``<file>.sig`` by default.

Errors: malformed key files raise :class:`SigningError` (a
:class:`~pengyos_sbom.errors.SBOMError`); missing files raise
:class:`FileNotFoundError`. Both map to exit code 1 in the CLI.
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Union

from . import ed25519
from .errors import SBOMError

__all__ = [
    "SigningError",
    "load_signing_key",
    "load_verification_key",
    "verification_key_candidates",
    "canonical_bytes",
    "sign_bytes",
    "sign_file",
    "verify_bytes",
    "verify_file",
]

PathLike = Union[str, Path]


class SigningError(SBOMError):
    """A key file is malformed or otherwise unusable for signing."""


# --- Key files ------------------------------------------------------------
def _decode_key_file(path: PathLike, what: str) -> bytes:
    """Read *path* and return the raw 32-byte key it contains.

    Accepts exactly-32-byte binary files or text files holding a single
    base64 string (whitespace-tolerant). Anything else is a
    :class:`SigningError` naming *what* the key is supposed to be.
    """
    path = Path(path)
    data = path.read_bytes()  # raises FileNotFoundError
    if len(data) == ed25519.SEED_LEN:
        return data
    text = data.decode("utf-8", errors="replace").strip()
    # base64 with or without padding; validate=True rejects non-alphabet chars
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(padded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise SigningError(
            f"{what} file {path}: not 32 raw bytes and not a valid base64 "
            f"key ({exc})"
        ) from exc
    if len(raw) != ed25519.SEED_LEN:
        raise SigningError(
            f"{what} file {path}: decoded {len(raw)} bytes, expected "
            f"{ed25519.SEED_LEN}"
        )
    return raw


def load_signing_key(path: PathLike) -> bytes:
    """Load an ed25519 *signing* key (32-byte secret-key seed) from *path*."""
    return _decode_key_file(path, "signing key")


def load_verification_key(path: PathLike) -> bytes:
    """Load an ed25519 *verification* key (32-byte public key) from *path*.

    If the decoded bytes are a valid curve point they are the public key;
    otherwise they are treated as a secret-key seed and the matching public
    key is derived. See :func:`verification_key_candidates` for the full
    file-level semantics used by ``verify`` (which tries *both*
    interpretations).

    .. note::
       For a key file that is *also* a valid curve point (true of ~50% of
       random seeds), this returns the raw bytes — which is correct when the
       file holds a public key, but not when it holds an on-curve seed.
       :func:`verify_bytes` / :func:`verify_file` therefore resolve the file
       via :func:`verification_key_candidates` and try every candidate.
    """
    raw = _decode_key_file(path, "verification key")
    if ed25519.is_on_curve(raw):
        return raw
    return ed25519.publickey(raw)


def verification_key_candidates(path: PathLike) -> list:
    """Return the public keys a ``verify --key`` file may denote.

    Every 32-byte key file has (up to) two valid interpretations:

    * the decoded bytes **as a public key**, when they are a valid curve
      point;
    * the decoded bytes **as a secret-key seed**, from which the matching
      public key is derived — always, since derivation is deterministic and
      valid for any 32-byte input.

    The two are ambiguous: an ed25519 seed is itself a valid curve point
    with ~50% probability. Returning both candidates (deduplicated, public
    key first) lets :func:`verify_bytes` accept the signature iff *any*
    candidate verifies — which makes the promise "the file that signed can
    verify" hold for every key, including on-curve seeds.
    """
    raw = _decode_key_file(path, "verification key")
    candidates = []
    if ed25519.is_on_curve(raw):
        candidates.append(raw)
    derived = ed25519.publickey(raw)
    if derived not in candidates:
        candidates.append(derived)
    return candidates


# --- Canonicalization -------------------------------------------------------
# The fixed placeholder the creation timestamp is normalized to inside the
# hashed body. Because it is a *constant*, two SBOMs that differ only in the
# wall-clock ``created`` / ``timestamp`` field canonicalize to identical bytes
# and therefore hash to the same value.
_TIMESTAMP_NORMALIZED = "1970-01-01T00:00:00+00:00"


def _canonicalize(doc: dict) -> dict:
    """Return a deep copy of *doc* with the creation timestamp normalized out.

    SPDX documents carry ``creationInfo.created``; CycloneDX documents carry
    ``metadata.timestamp``. Normalizing (not deleting) keeps the key present and
    its type a string, so the shape stays stable whether or not the field was
    present in the original. Only the timestamp is touched — the rest of the
    document's *semantic* content is preserved byte-for-byte after re-serialization.
    """
    out = json.loads(json.dumps(doc))  # deep copy via the JSON round-trip
    if "spdxVersion" in out:
        info = out.get("creationInfo")
        if isinstance(info, dict):
            info["created"] = _TIMESTAMP_NORMALIZED
    elif "bomFormat" in out:
        meta = out.get("metadata")
        if isinstance(meta, dict):
            meta["timestamp"] = _TIMESTAMP_NORMALIZED
    return out


def canonical_bytes(data: bytes) -> bytes:
    """Return the byte string that sign/verify actually hash.

    The signature binds the document's *semantic content*, not its exact
    on-disk serialization. For a JSON SPDX 2.3 or CycloneDX SBOM this means:

    1. parse the JSON;
    2. normalize the wall-clock creation timestamp out of the hashed body
       (``creationInfo.created`` for SPDX, ``metadata.timestamp`` for
       CycloneDX) — replaced by a fixed constant, so a file re-generated at a
       later instant (or a copy whose timestamp was stripped/shifted) still
       hashes identically;
    3. re-serialize canonically (sorted keys, compact separators, ASCII-escaped
       non-ASCII) so key order and indentation never affect the hash.

    The result is stable across re-serialization: whitespace, key order and the
    creation timestamp are all normalized away, while any change to *content*
    (a version, a license, an added/removed component, a tampered checksum) is
    preserved and therefore still detected. Because *both* ``sign`` and
    ``verify`` route through this one function, the round-trip invariant holds
    whatever it returns. Non-JSON bytes fall back to the identity (the exact
    bytes are bound), which is the safe choice for anything this tool does not
    recognize as an SBOM.
    """
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return data
    if not isinstance(doc, dict):
        return data
    return json.dumps(
        _canonicalize(doc),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


# --- Sign / verify ----------------------------------------------------------
def sign_bytes(data: bytes, seed: bytes) -> bytes:
    """Sign the canonical form of *data*; returns the 64-byte signature.

    :raises SigningError: if *seed* is not 32 bytes.
    """
    if len(seed) != ed25519.SEED_LEN:
        raise SigningError(f"ed25519 seed must be {ed25519.SEED_LEN} bytes")
    return ed25519.sign(canonical_bytes(data), seed)


def verify_bytes(data: bytes, signature: bytes, pub) -> bool:
    """Check *signature* over the canonical form of *data* against *pub*.

    *pub* is a single 32-byte public key, or a *list* of candidate public
    keys (see :func:`verification_key_candidates`) — the signature is
    accepted iff it verifies against at least one candidate. The candidate
    list is how ``verify --key`` resolves a key file that may hold either a
    public key or a seed (the two interpretations are ambiguous: ~50% of
    random seeds are themselves valid curve points).
    """
    cands = pub if isinstance(pub, (list, tuple)) else [pub]
    if any(len(p) != ed25519.PUBLIC_KEY_LEN for p in cands):
        return False
    body = canonical_bytes(data)
    return any(ed25519.verify(body, signature, p) for p in cands)


def sign_file(path: PathLike, seed: bytes) -> bytes:
    """Sign the file at *path*; returns the 64-byte detached signature.

    :raises FileNotFoundError: if the file does not exist.
    :raises SigningError: if *seed* is not 32 bytes.
    """
    return sign_bytes(Path(path).read_bytes(), seed)


def verify_file(path: PathLike, pub, signature: bytes) -> bool:
    """Check the detached *signature* over the file at *path* against *pub*.

    *pub* may be a single 32-byte public key or the candidate list from
    :func:`verification_key_candidates`. See :func:`verify_bytes` for the
    candidate semantics.

    :raises FileNotFoundError: if the file does not exist.
    """
    return verify_bytes(Path(path).read_bytes(), signature, pub)
