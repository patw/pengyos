"""Tests for the pengyos-sig-v1 wire format (signature_format.py)."""

from __future__ import annotations

import hashlib

from pengyos_verifier import signature_format as sf

KEYID = "ab" * 32
DIGEST = "cd" * 32
VALID_HEADER = f"{sf.MAGIC} {sf.VERSION} {sf.ALGORITHM} {KEYID} {sf.HASH_ALGORITHM} {DIGEST}"
BODY = bytes(range(64))


def make_sig(raw_header: str = VALID_HEADER, body: bytes = BODY) -> bytes:
    return raw_header.encode("ascii") + b"\n" + body


# --- header parsing ---------------------------------------------------------

def test_valid_header_parses():
    h = sf.parse_sig_header_line(VALID_HEADER)
    assert h is not None
    assert h.magic == sf.MAGIC
    assert h.version == 1
    assert h.algorithm == "ed25519"
    assert h.keyid == KEYID
    assert h.hash_algorithm == "sha256"
    assert h.hash == DIGEST


def test_header_shape_rules():
    bad = [
        "pengyos-sig-v0 1 ed25519 abcd sha256 ef01",  # wrong magic (short)
        "nope 1 ed25519 " + KEYID + " sha256 " + DIGEST,  # wrong magic
        f"{sf.MAGIC} 2 ed25519 {KEYID} sha256 {DIGEST}",  # unknown version
        f"{sf.MAGIC} x ed25519 {KEYID} sha256 {DIGEST}",  # non-numeric version
        f"{sf.MAGIC} 1 rsa {KEYID} sha256 {DIGEST}",  # wrong algorithm
        f"{sf.MAGIC} 1 ed25519 {KEYID} md5 {DIGEST}",  # wrong hash-alg
        f"{sf.MAGIC} 1 ed25519 {KEYID[:10]} sha256 {DIGEST}",  # short keyid
        f"{sf.MAGIC} 1 ed25519 {'AB' * 32} sha256 {DIGEST}",  # upper keyid
        f"{sf.MAGIC} 1 ed25519 {KEYID} sha256 {DIGEST[:10]}",  # short hash
        f"{sf.MAGIC} 1 ed25519 {KEYID} sha256",  # 5 fields
        f"{sf.MAGIC} 1 ed25519 {KEYID} sha256 {DIGEST} extra",  # 7 fields
    ]
    for line in bad:
        assert sf.parse_sig_header_line(line) is None, line


# --- header error messages ---------------------------------------------------

def test_sig_header_errors_empty_for_valid():
    assert sf.sig_header_errors(VALID_HEADER) == []


def test_sig_header_errors_pinpoint_the_problem():
    assert sf.sig_header_errors("a b c d e f g")[
        0].startswith("header must have exactly 6")
    assert sf.sig_header_errors(
        f"nope 1 ed25519 {KEYID} sha256 {DIGEST}"
    )[0] == f"bad magic 'nope' (expected 'pengyos-sig-v1')"
    assert sf.sig_header_errors(
        f"{sf.MAGIC} 2 ed25519 {KEYID} sha256 {DIGEST}"
    )[0].startswith("bad version")
    assert sf.sig_header_errors(
        f"{sf.MAGIC} 1 rsa {KEYID} sha256 {DIGEST}"
    )[0].startswith("bad algorithm")
    assert sf.sig_header_errors(
        f"{sf.MAGIC} 1 ed25519 {KEYID} md5 {DIGEST}"
    )[0].startswith("bad hash-algorithm")
    assert "keyid must be 64 lowercase hex chars" in sf.sig_header_errors(
        f"{sf.MAGIC} 1 ed25519 {'0' * 32} sha256 {DIGEST}")
    assert "hash must be 64 lowercase hex chars" in sf.sig_header_errors(
        f"{sf.MAGIC} 1 ed25519 {KEYID} sha256 {'0' * 32}")


# --- whole-file structure -----------------------------------------------------

def test_valid_sig_file_has_no_errors():
    assert sf.sig_file_errors(make_sig()) == []


def test_sig_file_structure_rules():
    assert sf.sig_file_errors(b"")[0] == "empty signature file"
    assert sf.sig_file_errors(make_sig().replace(b"\n", b" "))[
        0] == "missing newline after header line"
    assert sf.sig_file_errors(make_sig(body=BODY[:63]))[
        0].startswith("body must be exactly 64 bytes")
    assert sf.sig_file_errors(make_sig(body=BODY + b"x"))[
        0].startswith("body must be exactly 64 bytes")
    assert sf.sig_file_errors(b"b\xff\n" + BODY)[
        0] == "header line is not ASCII"


# --- digest binding -----------------------------------------------------------

def test_sha256_file_streams_and_matches_hashlib(tmp_path):
    payload = b"pengyos rootfs " * 100_000  # > 1 MB: exercises chunking
    artifact = tmp_path / "images" / "rootfs.ext4"
    artifact.parent.mkdir()
    artifact.write_bytes(payload)
    assert sf.sha256_file(str(artifact)) == \
        hashlib.sha256(payload).hexdigest()


def test_signature_binds_to_the_exact_artifact(tmp_path):
    artifact = tmp_path / "bzImage"
    artifact.write_bytes(b"kernel")
    digest = sf.sha256_file(str(artifact))
    sig = make_sig(raw_header=(
        f"{sf.MAGIC} 1 ed25519 {KEYID} sha256 {digest}"))
    assert sf.sig_file_errors(sig) == []
    header = sf.parse_sig_header_line(sig.split(b"\n", 1)[0].decode())
    assert header.hash == digest
    # Tamper the artifact: the embedded digest no longer binds.
    artifact.write_bytes(b"kernel-tampered")
    assert sf.sha256_file(str(artifact)) != header.hash
