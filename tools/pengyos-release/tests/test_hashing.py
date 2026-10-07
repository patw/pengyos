"""Tests for sha256 digest computation over bytes and files.

These exercise the *real* digest path the release tool uses to fingerprint
build artifacts — the digest recorded in an artifact must match ``sha256sum``
of the file on disk.
"""

from __future__ import annotations

import hashlib

import pytest

from pengyos_release.errors import ManifestError
from pengyos_release.manifest import (
    DIGEST_ALGORITHM,
    is_sha256_hex,
    sha256_bytes,
    sha256_file,
)


def test_sha256_bytes_matches_hashlib() -> None:
    data = b"pengyos-rootfs"
    assert sha256_bytes(data) == hashlib.sha256(data).hexdigest()


def test_sha256_bytes_known_vector() -> None:
    # sha256("") — the canonical empty-string vector, pins the implementation.
    assert sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sha256_bytes_is_lowercase_hex() -> None:
    assert is_sha256_hex(sha256_bytes(b"abc")) is True
    assert is_sha256_hex("deadbeef") is False  # too short
    assert is_sha256_hex("DEADBEEF" * 8) is False  # uppercase
    assert is_sha256_hex("z" * 64) is False  # non-hex


def test_digest_algorithm_constant() -> None:
    assert DIGEST_ALGORITHM == "sha256"


def test_sha256_file_matches_bytes(tmp_path) -> None:
    f = tmp_path / "rootfs.img"
    content = b"PENGYOS-ROOTFS\n" * 1000  # >1 MiB? no, but multi-write anyway
    f.write_bytes(content)
    assert sha256_file(f) == sha256_bytes(content)


def test_sha256_file_large_is_streaming(tmp_path) -> None:
    # A file larger than the read chunk must still hash correctly: proves the
    # streaming loop handles multiple blocks, not just a single read().
    f = tmp_path / "big.img"
    content = b"x" * (1024 * 1024 + 1234)  # just over one chunk
    f.write_bytes(content)
    assert sha256_file(f) == hashlib.sha256(content).hexdigest()


def test_sha256_file_missing_raises(tmp_path) -> None:
    with pytest.raises(ManifestError):
        sha256_file(tmp_path / "nope.img")
