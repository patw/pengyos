"""Tests for the pure-Python Ed25519 primitive (``pengyos_secureboot_enroll.ed25519``).

The correctness pin is the official RFC 8032 §7.1 test vectors (Ed25519
TEST 1..3, transcribed from the RFC text). Both directions are checked:
our ``publickey``/``sign`` must reproduce the RFC bytes exactly, and our
``verify`` must accept them. Negative cases cover tampered messages,
tampered signatures, wrong/invalid keys, malformed lengths, and the
``is_on_curve`` helper used by the key-file loader.

Pure data, no I/O, no network, no external tool.
"""

from __future__ import annotations

import pytest

from pengyos_secureboot_enroll import ed25519

# RFC 8032 §7.1 — Ed25519 test vectors (secret key, public key, message,
# signature), exactly as printed in the RFC.
RFC8032_VECTORS = [
    pytest.param(
        bytes.fromhex(
            "9d61b19deffd5a60ba844af492ec2cc4"
            "4449c5697b326919703bac031cae7f60"
        ),
        bytes.fromhex(
            "d75a980182b10ab7d54bfed3c964073a"
            "0ee172f3daa62325af021a68f707511a"
        ),
        b"",
        bytes.fromhex(
            "e5564300c360ac729086e2cc806e828a"
            "84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46b"
            "d25bf5f0595bbe24655141438e7a100b"
        ),
        id="rfc8032-test1-empty-message",
    ),
    pytest.param(
        bytes.fromhex(
            "4ccd089b28ff96da9db6c346ec114e0f"
            "5b8a319f35aba624da8cf6ed4fb8a6fb"
        ),
        bytes.fromhex(
            "3d4017c3e843895a92b70aa74d1b7ebc"
            "9c982ccf2ec4968cc0cd55f12af4660c"
        ),
        bytes.fromhex("72"),
        bytes.fromhex(
            "92a009a9f0d4cab8720e820b5f642540"
            "a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c"
            "387b2eaeb4302aeeb00d291612bb0c00"
        ),
        id="rfc8032-test2-1-byte",
    ),
    pytest.param(
        bytes.fromhex(
            "c5aa8df43f9f837bedb7442f31dcb7b1"
            "66d38535076f094b85ce3a2e0b4458f7"
        ),
        bytes.fromhex(
            "fc51cd8e6218a1a38da47ed00230f058"
            "0816ed13ba3303ac5deb911548908025"
        ),
        bytes.fromhex("af82"),
        bytes.fromhex(
            "6291d657deec24024827e69c3abe01a3"
            "0ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc659"
            "4a7c15e9716ed28dc027beceea1ec40a"
        ),
        id="rfc8032-test3-2-byte",
    ),
]


# --- RFC 8032 vectors ----------------------------------------------------
@pytest.mark.parametrize("seed,pub,msg,sig", RFC8032_VECTORS)
def test_publickey_matches_rfc(seed, pub, msg, sig):
    assert ed25519.publickey(seed) == pub


@pytest.mark.parametrize("seed,pub,msg,sig", RFC8032_VECTORS)
def test_sign_matches_rfc(seed, pub, msg, sig):
    # Ed25519 is deterministic: signature bytes must match the RFC exactly.
    assert ed25519.sign(msg, seed) == sig


@pytest.mark.parametrize("seed,pub,msg,sig", RFC8032_VECTORS)
def test_verify_accepts_rfc_signature(seed, pub, msg, sig):
    assert ed25519.verify(msg, sig, pub) is True


# --- Determinism and key derivation --------------------------------------
def test_publickey_is_deterministic():
    seed = bytes(range(32))
    assert ed25519.publickey(seed) == ed25519.publickey(seed)


def test_distinct_seeds_give_distinct_keys():
    assert ed25519.publickey(bytes(32)) != ed25519.publickey(bytes([1]) + bytes(31))


def test_sign_is_deterministic():
    seed = bytes(range(32))
    msg = b"reproducible pengyos secure boot"
    assert ed25519.sign(msg, seed) == ed25519.sign(msg, seed)


def test_different_messages_differ():
    seed = bytes(range(32))
    assert ed25519.sign(b"a", seed) != ed25519.sign(b"b", seed)


# --- Negative verification ------------------------------------------------
def test_tampered_message_fails():
    seed, pub, msg, sig = RFC8032_VECTORS[0].values
    assert ed25519.verify(msg + b"tamper", sig, pub) is False
    assert ed25519.verify(b"other", sig, pub) is False


def test_tampered_signature_fails():
    seed, pub, msg, sig = RFC8032_VECTORS[2].values
    for i in (0, 31, 32, 63):  # flip a byte in R and in S
        bad = bytearray(sig)
        bad[i] ^= 1
        assert ed25519.verify(msg, bytes(bad), pub) is False


def test_wrong_key_fails():
    seed, pub, msg, sig = RFC8032_VECTORS[1].values
    other_pub = ed25519.publickey(bytes(32))
    assert ed25519.verify(msg, sig, other_pub) is False


@pytest.mark.parametrize(
    "bad_sig",
    [
        b"",  # empty
        b"\x00" * 32,  # too short
        b"\x00" * 65,  # too long
    ],
)
def test_malformed_signature_fails(bad_sig):
    seed, pub, msg, sig = RFC8032_VECTORS[0].values
    assert ed25519.verify(msg, bad_sig, pub) is False


@pytest.mark.parametrize(
    "bad_pub",
    [
        b"",
        b"\x00" * 31,
        b"\x00" * 33,
        # y >= p: definitely not a valid compression
        (2**255 - 19 - 1).to_bytes(32, "little"),
    ],
)
def test_malformed_pubkey_fails(bad_pub):
    seed, pub, msg, sig = RFC8032_VECTORS[0].values
    assert ed25519.verify(msg, sig, bad_pub) is False


@pytest.mark.parametrize("seed_size", [0, 16, 31, 33, 64])
def test_bad_seed_size_raises(seed_size):
    with pytest.raises(ValueError):
        ed25519.publickey(bytes(seed_size))
    with pytest.raises(ValueError):
        ed25519.sign(b"msg", bytes(seed_size))


# --- is_on_curve -----------------------------------------------------------
def test_is_on_curve_true_for_valid_pubkey():
    _, pub, _, _ = RFC8032_VECTORS[0].values
    assert ed25519.is_on_curve(pub) is True


def test_is_on_curve_true_for_all_rfc_pubkeys():
    for param in RFC8032_VECTORS:
        assert ed25519.is_on_curve(param.values[1]) is True


def test_is_on_curve_false_for_off_curve_bytes():
    # Deterministic scan: the first few 32-byte patterns that fail the
    # curve check. (Note: b"\x00" * 32 is NOT a good negative case — it
    # encodes the valid point with y = 0.)
    off_curve = None
    for k in range(1, 256):
        cand = bytes([k]) + bytes(31)
        if not ed25519.is_on_curve(cand):
            off_curve = cand
            break
    assert off_curve is not None
    assert ed25519.is_on_curve(off_curve) is False


def test_is_on_curve_false_for_wrong_length():
    assert ed25519.is_on_curve(b"") is False
    assert ed25519.is_on_curve(b"\x00" * 31) is False
    assert ed25519.is_on_curve(b"\x00" * 33) is False


def test_flipped_signbit_is_a_different_valid_point():
    # Flipping the parity bit selects the *other* root (p - x, y), which is
    # still a valid curve point — but a different key, so it must reject the
    # signature made for the original key.
    _, pub, msg, sig = RFC8032_VECTORS[0].values
    flipped = bytearray(pub)
    flipped[31] ^= 0x80
    assert ed25519.is_on_curve(bytes(flipped)) is True
    assert ed25519.verify(msg, sig, bytes(flipped)) is False
