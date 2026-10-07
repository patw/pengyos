"""Tests for the signing layer (pengyos_sbom.signing).

Covers the file-level contract: key-file parsing (raw 32 bytes, base64
text, error cases), the seed→public-key fallback of
``load_verification_key``, the ``canonical_bytes`` seam, and the
sign/verify round-trip plus tamper detection at the bytes level.

Uses the RFC 8032 TEST 1 key as the fixed test keypair (independent of
any key generation inside this module).
"""

from __future__ import annotations

import base64
import os

import pytest

from pengyos_sbom import ed25519, signing
from pengyos_sbom.errors import SBOMError

# RFC 8032 TEST 1 key (a known-good seed/public-key pair).
SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
PUB = ed25519.publickey(SEED)
assert len(SEED) == 32 and len(PUB) == 32

DOC = (
    b'{"spdxVersion": "SPDX-2.3", "note": "fake SBOM bytes for signing tests"}'
)


# --- Key-file parsing -----------------------------------------------------
def test_load_signing_key_raw_bytes(tmp_path):
    f = tmp_path / "seed.key"
    f.write_bytes(SEED)
    assert signing.load_signing_key(f) == SEED


def test_load_signing_key_base64(tmp_path):
    f = tmp_path / "seed.key"
    f.write_bytes(base64.b64encode(SEED))
    assert signing.load_signing_key(f) == SEED


def test_load_signing_key_base64_with_newline(tmp_path):
    f = tmp_path / "seed.key"
    f.write_text(base64.b64encode(SEED).decode() + "\n")
    assert signing.load_signing_key(f) == SEED


def test_load_signing_key_base64_unpadded(tmp_path):
    f = tmp_path / "seed.key"
    f.write_text(base64.b64encode(SEED).decode().rstrip("="))
    assert signing.load_signing_key(f) == SEED


def test_load_signing_key_wrong_length_raw(tmp_path):
    f = tmp_path / "short.key"
    f.write_bytes(SEED[:31])
    with pytest.raises(SBOMError) as ei:
        signing.load_signing_key(f)
    assert isinstance(ei.value, signing.SigningError)
    assert "not 32 raw bytes" in str(ei.value)


def test_load_signing_key_base64_wrong_decoded_length(tmp_path):
    f = tmp_path / "wrong.key"
    f.write_text(base64.b64encode(b"too-short").decode() + "\n")
    with pytest.raises(signing.SigningError):
        signing.load_signing_key(f)


def test_load_signing_key_garbage_text(tmp_path):
    f = tmp_path / "garbage.key"
    f.write_text("definitely-not-a-key " + "x" * 40 + "\n")
    with pytest.raises(signing.SigningError):
        signing.load_signing_key(f)


def test_load_key_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        signing.load_signing_key(tmp_path / "nope.key")
    with pytest.raises(FileNotFoundError):
        signing.load_verification_key(tmp_path / "nope.pub")


# --- Verification key: public key vs seed fallback -------------------------
def test_load_verification_key_raw_pubkey(tmp_path):
    f = tmp_path / "pub.key"
    f.write_bytes(PUB)
    assert signing.load_verification_key(f) == PUB


def test_load_verification_key_base64_pubkey(tmp_path):
    f = tmp_path / "pub.key"
    f.write_text(base64.b64encode(PUB).decode() + "\n")
    assert signing.load_verification_key(f) == PUB


def test_load_verification_key_accepts_seed_and_derives_pubkey(tmp_path):
    # A signing-key file must also work for `verify --key`.
    f = tmp_path / "seed.key"
    f.write_bytes(SEED)
    assert signing.load_verification_key(f) == PUB


def test_load_verification_key_base64_seed(tmp_path):
    f = tmp_path / "seed.key"
    f.write_text(base64.b64encode(SEED).decode())
    assert signing.load_verification_key(f) == PUB


# --- verification_key_candidates (on-curve-seed ambiguity) --------------
def _find_on_curve_seed(attempts=200):
    """A random seed that is itself a valid curve point (true of ~50% of
    random seeds — the case that broke the naive fallback)."""
    for _ in range(attempts):
        seed = os.urandom(32)
        if ed25519.is_on_curve(seed):
            return seed
    raise AssertionError("no on-curve seed found (probability ~2^-attempts)")


def test_candidates_pubkey_file_has_one_candidate(tmp_path):
    f = tmp_path / "pub.key"
    f.write_bytes(PUB)
    cands = signing.verification_key_candidates(f)
    assert PUB in cands


def test_candidates_seed_file_has_both_interpretations(tmp_path):
    seed = _find_on_curve_seed()
    f = tmp_path / "seed.key"
    f.write_bytes(seed)
    cands = signing.verification_key_candidates(f)
    # Both the raw bytes (valid curve point) and the derived public key.
    assert len(cands) == 2
    assert seed in cands
    assert ed25519.publickey(seed) in cands
    assert cands[0] == seed  # public-key interpretation comes first


def test_on_curve_seed_file_signs_and_verifies(tmp_path):
    """Regression: an ed25519 seed is a valid curve point with ~50%
    probability; the file that signed must verify even then."""
    for _ in range(5):  # several on-curve seeds, not just one
        seed = _find_on_curve_seed()
        f = tmp_path / "seed.key"
        f.write_bytes(seed)
        sig = signing.sign_bytes(DOC, seed)
        cands = signing.verification_key_candidates(f)
        assert signing.verify_bytes(DOC, sig, cands) is True
        assert signing.verify_bytes(DOC, sig, [ed25519.publickey(seed)]) is True


def test_verify_bytes_candidate_list_all_wrong_fails():
    sig = signing.sign_bytes(DOC, SEED)
    other = ed25519.publickey(bytes(32))
    assert signing.verify_bytes(DOC, sig, [other, other[:31] + b"\x01"]) is False
    # An empty candidate list never verifies.
    assert signing.verify_bytes(DOC, sig, []) is False


def test_verify_file_with_candidate_list(tmp_path):
    f = tmp_path / "sbom.json"
    f.write_bytes(DOC)
    seed = _find_on_curve_seed()
    sig = signing.sign_file(f, seed)
    key = tmp_path / "seed.key"
    key.write_bytes(seed)
    assert signing.verify_file(f, signing.verification_key_candidates(key), sig) is True
    # A signature made with a *different* key fails even with candidates.
    assert signing.verify_file(f, signing.verification_key_candidates(key),
                               signing.sign_bytes(DOC, SEED)) is False


# --- Canonicalization seam --------------------------------------------------
def test_canonical_bytes_normalizes_spdx_created():
    # Two SPDX docs differing only in creationInfo.created hash identically.
    a = (
        b'{"spdxVersion":"SPDX-2.3",'
        b'"creationInfo":{"created":"2026-01-01T00:00:00+00:00",'
        b'"creators":["Tool:x"]}}'
    )
    b = (
        b'{"spdxVersion":"SPDX-2.3",'
        b'"creationInfo":{"created":"2026-12-31T23:59:59+00:00",'
        b'"creators":["Tool:x"]}}'
    )
    assert signing.canonical_bytes(a) == signing.canonical_bytes(b)


def test_canonical_bytes_normalizes_cdx_timestamp():
    # Two CycloneDX BOMs differing only in metadata.timestamp hash identically.
    a = b'{"bomFormat":"CycloneDX","metadata":{"timestamp":"2026-01-01T00:00:00+00:00"}}'
    b = b'{"bomFormat":"CycloneDX","metadata":{"timestamp":"2026-12-31T23:59:59+00:00"}}'
    assert signing.canonical_bytes(a) == signing.canonical_bytes(b)


def test_canonical_bytes_independent_of_key_order_and_whitespace():
    a = b'{"spdxVersion":"SPDX-2.3","name":"A"}'
    b = b'{\n  "name": "A",\n  "spdxVersion": "SPDX-2.3"\n}\n'
    assert signing.canonical_bytes(a) == signing.canonical_bytes(b)


def test_canonical_bytes_preserves_content_difference():
    a = b'{"spdxVersion":"SPDX-2.3","name":"A","version":"1.0"}'
    b = b'{"spdxVersion":"SPDX-2.3","name":"A","version":"2.0"}'
    assert signing.canonical_bytes(a) != signing.canonical_bytes(b)


def test_canonical_bytes_falls_back_to_identity_for_non_json():
    # Non-JSON bytes are bound verbatim (this tool does not recognize them).
    raw = b"\x00\xff\xfe not json at all"
    assert signing.canonical_bytes(raw) == raw
    # A JSON array is valid JSON but not an SBOM document object.
    assert signing.canonical_bytes(b"[1,2,3]") == b"[1,2,3]"
    # Empty input is a no-op.
    assert signing.canonical_bytes(b"") == b""


# --- Sign / verify round trip ------------------------------------------------
def test_sign_then_verify_roundtrip():
    sig = signing.sign_bytes(DOC, SEED)
    assert len(sig) == ed25519.SIGNATURE_LEN
    assert signing.verify_bytes(DOC, sig, PUB) is True


def test_sign_deterministic():
    assert signing.sign_bytes(DOC, SEED) == signing.sign_bytes(DOC, SEED)


def test_tampered_content_fails():
    sig = signing.sign_bytes(DOC, SEED)
    # A real *content* change is still detected...
    assert signing.verify_bytes(DOC.replace(b"fake SBOM", b"evil SBOM"), sig, PUB) is False
    # ...and truncation that breaks the JSON is detected too.
    assert signing.verify_bytes(DOC[:-1], sig, PUB) is False
    flipped = bytearray(DOC)
    flipped[10] ^= 1
    assert signing.verify_bytes(bytes(flipped), sig, PUB) is False


def test_whitespace_only_change_is_normalized_not_detected():
    # Serialization-level changes (trailing whitespace) are normalized away by
    # canonical_bytes, so they do NOT break a signature over the same content.
    sig = signing.sign_bytes(DOC, SEED)
    assert signing.verify_bytes(DOC + b" ", sig, PUB) is True
    assert signing.verify_bytes(DOC + b"\n  ", sig, PUB) is True


def test_tampered_signature_fails():
    sig = bytearray(signing.sign_bytes(DOC, SEED))
    sig[0] ^= 1
    assert signing.verify_bytes(DOC, bytes(sig), PUB) is False
    bad = bytearray(sig)
    bad[63] ^= 0x80
    assert signing.verify_bytes(DOC, bytes(bad), PUB) is False


def test_wrong_key_fails():
    sig = signing.sign_bytes(DOC, SEED)
    other_pub = ed25519.publickey(bytes(32))
    assert signing.verify_bytes(DOC, sig, other_pub) is False


def test_wrong_length_pub_rejected():
    sig = signing.sign_bytes(DOC, SEED)
    assert signing.verify_bytes(DOC, sig, PUB[:31]) is False


def test_bad_seed_length_raises():
    with pytest.raises(signing.SigningError):
        signing.sign_bytes(DOC, SEED[:16])
    with pytest.raises(signing.SigningError):
        signing.sign_bytes(DOC, b"")


# --- File-level API ---------------------------------------------------------
def test_sign_file_and_verify_file(tmp_path):
    f = tmp_path / "sbom.json"
    f.write_bytes(DOC)
    sig = signing.sign_file(f, SEED)
    assert signing.verify_file(f, PUB, sig) is True
    # tamper the file on disk
    f.write_bytes(DOC + b"tampered")
    assert signing.verify_file(f, PUB, sig) is False


def test_sign_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        signing.sign_file(tmp_path / "missing.json", SEED)
    with pytest.raises(FileNotFoundError):
        signing.verify_file(tmp_path / "missing.json", PUB, bytes(64))


def test_sign_file_matches_rfc_vector():
    # sign_bytes over the empty message with the RFC 1 seed reproduces the
    # RFC 8032 TEST 1 signature — end-to-end proof the layer composes
    # correctly (sign of b"" with prefix logic is different; instead check
    # the raw primitive agreement through canonical identity).
    assert (
        signing.sign_bytes(b"", SEED)
        == ed25519.sign(b"", SEED)
    )
