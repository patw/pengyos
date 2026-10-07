"""Tests for the signing abstraction."""

from __future__ import annotations

import pytest

from pengyos_release.errors import SigningError
from pengyos_release.signing import (
    HmacSigner,
    StubSigner,
    decode_signature,
    encode_signature,
    load_signer,
)


def test_hmac_signer_sign_verify_roundtrip() -> None:
    signer = HmacSigner(b"secret-key")
    data = b"hello pengyos"
    sig = signer.sign(data)
    assert signer.verify(sig, data) is True
    assert signer.verify(sig, b"different") is False


def test_hmac_signer_is_deterministic() -> None:
    signer = HmacSigner(b"key")
    assert signer.sign(b"data") == signer.sign(b"data")


def test_hmac_signer_key_changes_result() -> None:
    a = HmacSigner(b"key-a").sign(b"data")
    b = HmacSigner(b"key-b").sign(b"data")
    assert a != b


def test_stub_signer_verify_roundtrip() -> None:
    signer = StubSigner()
    data = b"payload"
    assert signer.verify(signer.sign(data), data) is True
    assert signer.verify(signer.sign(data), b"other") is False


def test_encode_decode_signature_roundtrip() -> None:
    signer = HmacSigner(b"key")
    raw = signer.sign(b"data")
    encoded = encode_signature(raw)
    assert isinstance(encoded, str)
    assert decode_signature(encoded) == raw


def test_encode_signature_is_urlsafe_no_padding() -> None:
    # Force a key/signature that produces + or / in base64 to check urlencoding.
    signer = HmacSigner(b"x" * 64)
    encoded = encode_signature(signer.sign(b"z" * 64))
    assert "+" not in encoded and "/" not in encoded
    assert "=" not in encoded


def test_load_signer_from_bytes() -> None:
    signer = load_signer(b"raw-key")
    assert isinstance(signer, HmacSigner)


def test_load_signer_from_file(tmp_path) -> None:
    keyfile = tmp_path / "key"
    keyfile.write_bytes(b"  key-from-file  \n")
    signer = load_signer(keyfile)
    assert signer.sign(b"data") == HmacSigner(b"key-from-file").sign(b"data")


def test_load_signer_missing_file_raises() -> None:
    with pytest.raises(SigningError):
        load_signer(tmp_path := __import__("pathlib").Path("/nonexistent/key-xyz"))


def test_load_signer_empty_key_raises() -> None:
    with pytest.raises(ValueError):
        load_signer(b"")
