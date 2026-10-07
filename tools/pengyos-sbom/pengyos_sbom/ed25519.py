"""Pure-Python Ed25519 (RFC 8032) — the signing primitive for SBOMs.

Why stdlib-only: CPython has no built-in ed25519 (``hashlib`` only provides
the *hash*, not the signature scheme), and the project rule is standard
library with zero runtime dependencies. This module is a self-contained
implementation of the RFC 8032 Ed25519 signature scheme that follows the
reference in **RFC 8032 Appendix C** (complete extended-coordinate point
arithmetic over the twisted-Edwards curve, double-and-add scalar
multiplication, ``a &= (1<<254)-8; a |= 1<<254`` key clamping). Its
correctness is pinned by the RFC 8032 §7 appendix test vectors in
``tests/test_ed25519.py``.

The API mirrors the RFC's terminology:

* a **secret key** is a uniformly random 32-byte string (a *seed*);
* a **public key** is the 32-byte compression of ``A = aG`` (the base point
  scaled by the clamped scalar ``a`` derived from the seed);
* a **signature** is 64 bytes: the 32-byte compression of ``R`` followed by
  the little-endian scalar ``S``.

Only SHA-512 (``hashlib``) is used as the auxiliary hash.
"""

from __future__ import annotations

import hashlib
from typing import Optional, Tuple

__all__ = [
    "SEED_LEN",
    "PUBLIC_KEY_LEN",
    "SIGNATURE_LEN",
    "publickey",
    "sign",
    "verify",
    "is_on_curve",
]

# --- Field / curve parameters (RFC 8032 §3.8, §5.1) ----------------------
# Field prime p = 2^255 - 19.
_P = 2**255 - 19
# Scalar space prime L (order of the base point).
_L = 2**252 + 27742317777372353535851937790883648493
# Twisted-Edwards constant d for Ed25519.
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
# sqrt(-1) mod p — used to fix the sign of a candidate square root.
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    """Multiplicative inverse mod p (x is non-zero by construction)."""
    return pow(x, _P - 2, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    """Recover x (with the given low bit) for the given y, or ``None``.

    Follows RFC 8032 Appendix C ``recover_x``, including the ``x2 == 0``
    special case (y = ±1) where an odd sign is rejected outright.
    """
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


# Base point G = (gx, gy) with gy = 4 * 5^-1 mod p.
_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
# G in extended homogeneous coordinates (X, Y, Z, T) with T = X*Y (the curve
# constant d is applied inside the addition formula, as in RFC 8032
# Appendix C), which keeps encoding/decoding free of d.
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(P: Tuple[int, int, int, int], Q: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Complete extended-coordinate addition (RFC 8032 Appendix C ``point_add``).

    Points store T = X*Y (without d); the formula applies d once:
    ``C = 2 * T1 * T2 * d``. Complete for all valid inputs, including the
    neutral element (0, 1, 1, 0).
    """
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


def _scalarmult(s: int, P: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    """Double-and-add scalar multiplication (``s * P``, RFC 8032 ``point_mul``)."""
    Q = (0, 1, 1, 0)  # the neutral element
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _encodepoint(P: Tuple[int, int, int, int]) -> bytes:
    """32-byte compression: little-endian y with the top bit = parity of x."""
    zinv = _inv(P[2])
    x = P[0] * zinv % _P
    y = P[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decodepoint(s: bytes) -> Optional[Tuple[int, int, int, int]]:
    """Decompress 32 bytes into extended coordinates, or ``None`` if invalid."""
    if len(s) != PUBLIC_KEY_LEN:
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
    """Derive the signing scalar ``a`` from the 32-byte seed (RFC 8032 §5.1.5).

    ``a &= (1 << 254) - 8; a |= (1 << 254)`` on the little-endian integer of
    the first 32 SHA-512 bytes, exactly as RFC 8032 Appendix C
    ``secret_expand`` (the form every real implementation uses).
    """
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a


def _h_mod_L(*chunks: bytes) -> int:
    """SHA-512 of the chunks reduced mod the group order L (``H mod l``)."""
    return int.from_bytes(hashlib.sha512(b"".join(chunks)).digest(), "little") % _L


SEED_LEN = 32
PUBLIC_KEY_LEN = 32
SIGNATURE_LEN = 64


def publickey(seed: bytes) -> bytes:
    """Derive the 32-byte public key from a 32-byte secret-key seed.

    :raises ValueError: if *seed* is not exactly 32 bytes.
    """
    if len(seed) != SEED_LEN:
        raise ValueError(f"ed25519 seed must be {SEED_LEN} bytes, got {len(seed)}")
    return _encodepoint(_scalarmult(_clamp(seed), _G))


def sign(message: bytes, seed: bytes) -> bytes:
    """Sign *message* with the 32-byte *seed*; returns a 64-byte signature.

    Deterministic (pure Ed25519, no randomness): same message + seed always
    gives the same signature.

    :raises ValueError: if *seed* is not exactly 32 bytes.
    """
    if len(seed) != SEED_LEN:
        raise ValueError(f"ed25519 seed must be {SEED_LEN} bytes, got {len(seed)}")
    h = hashlib.sha512(seed).digest()
    a = _clamp(seed)
    prefix = h[32:]
    A = _encodepoint(_scalarmult(a, _G))
    r = _h_mod_L(prefix, message)
    R = _encodepoint(_scalarmult(r, _G))
    S = (r + _h_mod_L(R, A, message) * a) % _L
    return R + S.to_bytes(SEED_LEN, "little")


def is_on_curve(pub: bytes) -> bool:
    """True if *pub* is the compression of a valid curve point.

    Used by :mod:`pengyos_sbom.signing` to tell a public key apart from a
    raw seed when loading a ``--key`` file for ``verify``.
    """
    if len(pub) != PUBLIC_KEY_LEN:
        return False
    return _decodepoint(pub) is not None


def verify(message: bytes, signature: bytes, pub: bytes) -> bool:
    """Check a 64-byte *signature* over *message* against a 32-byte public key.

    Returns ``True``/``False`` — never raises for malformed input (a
    malformed signature or public key simply fails verification).
    """
    if len(signature) != SIGNATURE_LEN or len(pub) != PUBLIC_KEY_LEN:
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
    # Accept iff S*G == R + k*A, comparing the canonical compressions.
    left = _encodepoint(_scalarmult(S, _G))
    right = _encodepoint(_add(R, _scalarmult(k, A)))
    result = 0
    for x, y in zip(left, right):
        result |= x ^ y
    return result == 0
