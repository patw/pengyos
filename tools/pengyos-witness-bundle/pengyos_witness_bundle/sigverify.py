"""SBOM signature verification for pengyos-witness-bundle.

Verifies the detached ed25519 signature that ``pengyos-build`` writes next to
each SBOM (``<sbom>.sig``), using the same scheme ``pengyos-sbom`` uses:

* **Key files** hold a 32-byte ed25519 key: raw bytes, or a single base64
  string (padding optional, surrounding whitespace ignored). A *verification*
  key is the 32-byte public key; the file may also hold a *seed*, from which the
  matching public key is derived. Because a 32-byte seed is itself a valid curve
  point ~50 % of the time, both interpretations are tried (see
  :func:`public_key_candidates`).
* **Canonicalization**: the signed byte string is ``canonical_bytes(raw)`` — the
  SBOM's *semantic* content, not its exact on-disk serialization. For a JSON
  SPDX 2.3 / CycloneDX document the creation timestamp is normalized out and the
  body is re-serialized canonically (sorted keys, compact separators,
  ASCII-escaped), so a signature is stable across the wall-clock timestamp and
  across whitespace / key-order differences, while any content change is still
  detected. Non-JSON bytes are bound verbatim (the identity fallback).
* **Detached signature**: a 64-byte binary file.

Verification prefers ``pengyos-sbom``'s own ``verify`` when it is importable —
so this tool agrees byte-for-byte with the signer — and otherwise falls back to
a **pure-Python, stdlib-only** RFC 8032 Ed25519 implementation (only
``hashlib.sha512`` is used) that mirrors ``pengyos-sbom``'s reference. A bad
signature is a hard fail: :func:`verify_file` raises :class:`VerifyError`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from pathlib import Path
from typing import Optional, Tuple, Union

__all__ = [
    "VerifyError",
    "canonical_bytes",
    "load_public_key",
    "public_key_candidates",
    "sign_bytes",
    "verify_file",
    "verify_bytes",
]

PathLike = Union[str, "Path"]

# The fixed placeholder the creation timestamp is normalized to inside the
# hashed body. Pinning it to a constant is what makes a signature survive the
# wall-clock ``created`` / ``timestamp`` field re-generated at a later instant.
_TIMESTAMP_NORMALIZED = "1970-01-01T00:00:00+00:00"


class VerifyError(Exception):
    """A verification-level failure.

    Raised by :func:`verify_file` when an input is missing or malformed, or when
    the signature does not verify. The CLI turns this into a clean, non-zero
    exit rather than a traceback.
    """


# --- Canonicalization -------------------------------------------------------
def _canonicalize(doc: dict) -> dict:
    """Return a deep copy of *doc* with the creation timestamp normalized out.

    SPDX documents carry ``creationInfo.created``; CycloneDX documents carry
    ``metadata.timestamp``. Normalizing (not deleting) keeps the key present and
    its type a string, so the shape stays stable whether or not the field was
    present in the original. Only the timestamp is touched — the rest of the
    document's *semantic* content is preserved after re-serialization.
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


# --- Key files --------------------------------------------------------------
def _decode_key_file(path: PathLike, what: str) -> bytes:
    """Read *path* and return the raw 32-byte key it contains.

    Accepts exactly-32-byte binary files or text files holding a single base64
    string (whitespace-tolerant). Anything else is a :class:`VerifyError`
    naming *what* the key is supposed to be.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise VerifyError(f"{what} file {path}: not found") from exc
    except OSError as exc:
        raise VerifyError(f"{what} file {path}: {exc}") from exc
    if len(data) == 32:
        return data
    text = data.decode("utf-8", errors="replace").strip()
    # base64 with or without padding; validate=True rejects non-alphabet chars
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(padded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise VerifyError(
            f"{what} file {path}: not 32 raw bytes and not a valid base64 "
            f"key ({exc})"
        ) from exc
    if len(raw) != 32:
        raise VerifyError(
            f"{what} file {path}: decoded {len(raw)} bytes, expected 32"
        )
    return raw


def load_public_key(path: PathLike) -> bytes:
    """Load the 32-byte ed25519 *public key* a verification *path* denotes.

    If the decoded bytes are a valid curve point they are the public key;
    otherwise they are treated as a secret-key seed and the matching public key
    is derived. See :func:`public_key_candidates` for the full file-level
    semantics used by :func:`verify_file` (which tries *both* interpretations).
    """
    raw = _decode_key_file(path, "public key")
    if _is_on_curve(raw):
        return raw
    return _publickey(raw)


def public_key_candidates(path: PathLike) -> list[bytes]:
    """Return the public keys a ``--pub`` file may denote.

    Every 32-byte key file has (up to) two valid interpretations:

    * the decoded bytes **as a public key**, when they are a valid curve point;
    * the decoded bytes **as a secret-key seed**, from which the matching
      public key is derived — always, since derivation is deterministic and
      valid for any 32-byte input.

    The two are ambiguous: an ed25519 seed is itself a valid curve point with
    ~50 % probability. Returning both candidates (deduplicated, public key
    first) lets :func:`verify_file` accept the signature iff *any* candidate
    verifies — which makes the promise "the file that signed can verify" hold
    for every key, including on-curve seeds.
    """
    raw = _decode_key_file(path, "public key")
    candidates: list[bytes] = []
    if _is_on_curve(raw):
        candidates.append(raw)
    derived = _publickey(raw)
    if derived not in candidates:
        candidates.append(derived)
    return candidates


# --- Verification -----------------------------------------------------------
def verify_bytes(data: bytes, signature: bytes, pub) -> bool:
    """Check *signature* over the canonical form of *data* against *pub*.

    *pub* is a single 32-byte public key, or a *list* of candidate public keys
    (see :func:`public_key_candidates`) — the signature is accepted iff it
    verifies against at least one candidate. Returns ``True``/``False``; a
    malformed signature or public key simply fails (never raises).
    """
    cands = pub if isinstance(pub, (list, tuple)) else [pub]
    if any(len(p) != 32 for p in cands):
        return False
    body = canonical_bytes(data)
    return any(_ed25519_verify(body, signature, p) for p in cands)


def verify_file(
    sbom_path: PathLike,
    sig_path: PathLike,
    pub_path: PathLike,
) -> bool:
    """Verify the detached *sig_path* over *sbom_path* against *pub_path*.

    Returns ``(verified, method)``: *verified* is ``True`` iff the signature
    verifies, and *method* names the backend that produced the answer
    (``"pengyos-sbom"`` or ``"stdlib-ed25519"``). Raises :class:`VerifyError` —
    a hard fail — when any input is missing or malformed, or when the signature
    does not verify against any candidate public key.

    Prefers ``pengyos-sbom``'s own verification when importable (so this tool
    agrees byte-for-byte with the signer); otherwise uses the bundled
    stdlib-only Ed25519.
    """
    sbom_path = Path(sbom_path)
    sig_path = Path(sig_path)
    pub_path = Path(pub_path)

    try:
        sbom = sbom_path.read_bytes()
    except FileNotFoundError as exc:
        raise VerifyError(f"sbom not found: {sbom_path}") from exc
    except OSError as exc:
        raise VerifyError(f"cannot read sbom {sbom_path}: {exc}") from exc

    try:
        signature = sig_path.read_bytes()
    except FileNotFoundError as exc:
        raise VerifyError(f"signature not found: {sig_path}") from exc
    except OSError as exc:
        raise VerifyError(f"cannot read signature {sig_path}: {exc}") from exc

    try:
        candidates = public_key_candidates(pub_path)
    except VerifyError as exc:
        raise VerifyError(str(exc)) from exc

    if len(signature) != 64:
        raise VerifyError(
            f"signature {sig_path} is {len(signature)} bytes, expected 64"
        )

    verified, method = _verify(sbom, signature, candidates)
    if not verified:
        raise VerifyError(
            f"SBOM signature verification FAILED for {sbom_path} "
            f"(signature {sig_path} does not match public key {pub_path})"
        )
    return verified, method


# --- pengyos-sbom, when importable -----------------------------------------
# Cached import of ``pengyos_sbom.signing`` (see :func:`_try_pengyos_sbom`).
# ``None`` means "not importable yet / import failed"; a module object means
# "use it". Attempted once and cached so the cost is paid a single time.
_pengyos_signing = None


def _try_pengyos_sbom() -> Optional[object]:
    """Return ``pengyos_sbom.signing`` if importable, else ``None``.

    Importing is attempted once and cached on the module so the (cheap) import
    cost is paid a single time per process.
    """
    global _pengyos_signing
    if _pengyos_signing is None:
        try:
            from pengyos_sbom import signing  # type: ignore

            _pengyos_signing = signing
        except Exception:  # pragma: no cover - import surface varies by env
            _pengyos_signing = False  # mark "definitely not importable"
    return _pengyos_signing or None


def _verify(sbom: bytes, signature: bytes, candidates: list[bytes]) -> bool:
    """Verify against *candidates*, preferring ``pengyos-sbom`` if present.

    Returns ``(verified, method)`` where *method* names the backend that
    produced the answer (``"pengyos-sbom"`` or ``"stdlib-ed25519"``).
    """
    signing = _try_pengyos_sbom()
    if signing is not None:
        # ``verify_bytes`` accepts either a single key or the candidate list.
        return bool(signing.verify_bytes(sbom, signature, candidates)), "pengyos-sbom"
    return verify_bytes(sbom, signature, candidates), "stdlib-ed25519"


# ---------------------------------------------------------------------------
# Pure-Python, stdlib-only Ed25519 (RFC 8032) — the fallback.
#
# CPython ships no ed25519 (``hashlib`` only provides the *hash*, not the
# signature scheme), and this tool is stdlib-only with zero runtime
# dependencies. This is a self-contained implementation of RFC 8032 that
# mirrors :mod:`pengyos_sbom.ed25519` — the same reference this tool falls
# back to — so it agrees byte-for-byte with the signer. Only SHA-512 is used.
# ---------------------------------------------------------------------------
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return 0 if not sign else None
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = (x * _I) % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(P, Q):
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e = (b - a) % _P
    f = (dd - c) % _P
    g = (dd + c) % _P
    h = (b + a) % _P
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _scalarmult(s: int, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _encodepoint(P):
    zinv = _inv(P[2])
    x = P[0] * zinv % _P
    y = P[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decodepoint(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s[:32], "little")
    signbit = y >> 255
    y &= (1 << 255) - 1
    if y >= _P:
        return None
    x = _recover_x(y, signbit)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _clamp(seed: bytes) -> int:
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a


def _h_mod_L(*chunks: bytes) -> int:
    return int.from_bytes(hashlib.sha512(b"".join(chunks)).digest(), "little") % _L


def _publickey(seed: bytes) -> bytes:
    """Derive the 32-byte public key from a 32-byte secret-key seed."""
    if len(seed) != 32:
        raise ValueError(f"ed25519 seed must be 32 bytes, got {len(seed)}")
    return _encodepoint(_scalarmult(_clamp(seed), _G))


def _is_on_curve(pub: bytes) -> bool:
    if len(pub) != 32:
        return False
    return _decodepoint(pub) is not None


def _ed25519_verify(message: bytes, signature: bytes, pub: bytes) -> bool:
    """Check a 64-byte *signature* over *message* against a 32-byte public key."""
    if len(signature) != 64 or len(pub) != 32:
        return False
    A = _decodepoint(pub)
    if A is None:
        return False
    R = _decodepoint(signature[:32])
    if R is None:
        return False
    S = int.from_bytes(signature[32:], "little")
    if S >= _L:
        return False
    k = _h_mod_L(signature[:32], pub, message)
    left = _encodepoint(_scalarmult(S, _G))
    right = _encodepoint(_add(R, _scalarmult(k, A)))
    result = 0
    for x, y in zip(left, right):
        result |= x ^ y
    return result == 0


def sign(message: bytes, seed: bytes) -> bytes:
    """Sign *message* with the 32-byte *seed*; return a 64-byte signature.

    Deterministic (PureEdDSA, no randomness): the same message + seed always
    yields the same signature (RFC 8032 §5.1.6). Used by the test-suite to
    exercise the pure-Python path end-to-end without ``pengyos-sbom``.
    """
    if len(seed) != 32:
        raise ValueError(f"ed25519 seed must be 32 bytes, got {len(seed)}")
    h = hashlib.sha512(seed).digest()
    a = _clamp(seed)
    prefix = h[32:]
    A = _encodepoint(_scalarmult(a, _G))
    r = _h_mod_L(prefix, message)
    R = _encodepoint(_scalarmult(r, _G))
    S = (r + _h_mod_L(R, A, message) * a) % _L
    return R + S.to_bytes(32, "little")


def sign_bytes(data: bytes, seed: bytes) -> bytes:
    """Sign the canonical form of *data* with *seed*; return the 64-byte sig."""
    return sign(canonical_bytes(data), seed)
