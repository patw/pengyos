"""Signing abstraction for the PengyOS release tool.

The release feed is signed so that install/update can detect tampering. Signing
is *injectable*: :func:`load_signer` builds a concrete signer from a key source,
and any object implementing :class:`Signer` can be substituted (tests use
:class:`StubSigner`).

The concrete implementation (:class:`HmacSigner`) is an HMAC-SHA256 keyed over
the raw bytes. It is deterministic, offline, and needs no keys on disk beyond
the one you point it at — perfect for a local, non-networked update feed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

# Signatures are stored base64 (urlsafe, no padding) inside the manifest so the
# feed stays plain text and diff-friendly.
_SIG_ALGO = "hmac-sha256"


@runtime_checkable
class Signer(Protocol):
    """The minimal signing contract. Anything duck-typing this works.

    ``@runtime_checkable`` lets :func:`isinstance` accept any object that
    implements ``sign``/``verify`` (e.g. :class:`HmacSigner`, :class:`StubSigner`),
    which :func:`pengyos_release.feed.validate_feed` relies on.
    """

    def sign(self, data: bytes) -> bytes: ...

    def verify(self, signature: bytes, data: bytes) -> bool: ...


class SignerABC(ABC):
    def __init__(self, name: str = _SIG_ALGO) -> None:
        self.name = name

    @abstractmethod
    def sign(self, data: bytes) -> bytes:
        """Return the signature bytes for ``data``."""

    @abstractmethod
    def verify(self, signature: bytes, data: bytes) -> bool:
        """Return True iff ``signature`` is valid for ``data``."""


class HmacSigner(SignerABC):
    """HMAC-SHA256 signer. Deterministic and offline."""

    def __init__(self, key: bytes, name: str = _SIG_ALGO) -> None:
        if not key:
            raise ValueError("signing key must be non-empty")
        super().__init__(name)
        self._key = key

    def sign(self, data: bytes) -> bytes:
        return hmac.new(self._key, data, hashlib.sha256).digest()

    def verify(self, signature: bytes, data: bytes) -> bool:
        return hmac.compare_digest(self.sign(data), signature)


class StubSigner(SignerABC):
    """Deterministic no-op signer for tests.

    The signature is ``b"stub:" + sha256(data)`` so it still exercises the
    base64 encode/decode path and ``verify`` behaves like a real signer.
    """

    def __init__(self, name: str = "stub") -> None:
        super().__init__(name)

    def sign(self, data: bytes) -> bytes:
        return b"stub:" + hashlib.sha256(data).digest()

    def verify(self, signature: bytes, data: bytes) -> bool:
        return signature == b"stub:" + hashlib.sha256(data).digest()


def encode_signature(signature: bytes) -> str:
    """Base64 (url-safe, no padding) a signature for storage in the manifest."""
    return base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def decode_signature(text: str) -> bytes:
    """Inverse of :func:`encode_signature`."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def load_signer(key: bytes | str | os.PathLike[str] | None, name: str = _SIG_ALGO) -> Signer:
    """Build a :class:`HmacSigner` from a key.

    ``key`` may be raw key bytes/str, or a path to a file whose (stripped)
    contents are the key. A missing file path raises :class:`SigningError`.
    """
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(key, (bytes, bytearray)):
        if not key:
            raise ValueError("signing key must be non-empty")
        return HmacSigner(bytes(key), name=name)
    # A path-like key: load its (stripped) contents.
    if key is not None and os.path.exists(key):  # type: ignore[arg-type]
        with open(key, "rb") as fh:
            loaded = fh.read().strip()
        if not loaded:
            raise ValueError("signing key must be non-empty")
        return HmacSigner(loaded, name=name)
    from .errors import SigningError

    raise SigningError(f"signing key file not found: {key!s}")
