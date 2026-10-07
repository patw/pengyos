"""Tests for ed25519 public-key verification (the ``--pub`` feature).

Covers the "verify .sig ed25519 signatures against a supplied public
key" subtask at three levels:

* ``signature_format.load_public_key`` / ``keyid_of`` — the key-file
  contract (raw 32 bytes or 64 hex chars) and the v1 keyid binding
  (``keyid == SHA-256(public_key)``);
* ``check_signatures`` with ``CheckContext(public_key=...)`` — a
  genuinely-signed build dir passes with the right key and fails with
  pinpoint evidence for a wrong keyid, a signature that does not
  verify, or a key-less regression (no key supplied = the old
  key-independent behavior, unchanged);
* the CLI boundary — ``check --pub FILE`` exits 0 on a verified build,
  1 with the specific signatures finding on a wrong key, and 2 (usage
  error, checks never run) on a missing or malformed key file.

Everything is offline: signatures are made with the in-tree ed25519
primitive (``tests.fixtures_build.build_signed_dir``), no network, no
root, no producer tools.
"""

from __future__ import annotations

import json
import os

import pytest

from pengyos_verifier import checks, ed25519, signature_format
from pengyos_verifier.checks_impl import (
    CheckContext,
    build_report,
    check_signatures,
    run_checks,
)
from pengyos_verifier.cli import build_parser, main
from pengyos_verifier.model import Status
from tests.fixtures_build import (
    BZIMAGE_BYTES,
    ROOTFS_BYTES,
    build_good_dir,
    build_signed_dir,
    make_sig_bytes,
)

SEED_A = bytes(range(32))          # signs the good dir
SEED_B = bytes([7]) + bytes(31)    # a different key
PUB_A = ed25519.publickey(SEED_A)
PUB_B = ed25519.publickey(SEED_B)


def evidence_text(finding) -> str:
    return " | ".join(finding.evidence)


def make_ctx(build_dir, public_key=None) -> CheckContext:
    return CheckContext(build_dir=str(build_dir), attest_path="",
                        public_key=public_key)


# ----------------------------------------------------------------------
# keyid_of
# ----------------------------------------------------------------------

def test_keyid_of_is_sha256_of_pubkey():
    import hashlib
    assert signature_format.keyid_of(PUB_A) == hashlib.sha256(PUB_A).hexdigest()
    assert signature_format.keyid_of(PUB_A) == signature_format.keyid_of(PUB_A)
    assert signature_format.keyid_of(PUB_A) != signature_format.keyid_of(PUB_B)


def test_keyid_of_rejects_wrong_length():
    with pytest.raises(ValueError):
        signature_format.keyid_of(b"")
    with pytest.raises(ValueError):
        signature_format.keyid_of(b"\x00" * 31)
    with pytest.raises(ValueError):
        signature_format.keyid_of(b"\x00" * 33)


# ----------------------------------------------------------------------
# load_public_key
# ----------------------------------------------------------------------

def test_load_public_key_raw_bytes(tmp_path):
    path = tmp_path / "pub.raw"
    path.write_bytes(PUB_A)
    assert signature_format.load_public_key(str(path)) == PUB_A


def test_load_public_key_hex_text(tmp_path):
    for text in (PUB_A.hex(), PUB_A.hex().upper(), f" {PUB_A.hex()}\n"):
        path = tmp_path / "pub.hex"
        path.write_text(text, encoding="ascii")
        assert signature_format.load_public_key(str(path)) == PUB_A


@pytest.mark.parametrize(
    "raw",
    [
        b"",                        # empty
        b"\x00" * 31,               # 31 raw bytes
        b"\x00" * 33,               # 33 raw bytes
        b"not hex at all\n",        # text, not hex
        b"ff" * 32 + b"ff",         # 66 hex chars
        b"\x00\xff\xfe" * 10,       # 30 binary bytes, not ASCII
    ],
)
def test_load_public_key_rejects_malformed(tmp_path, raw):
    path = tmp_path / "pub.bad"
    path.write_bytes(raw)
    with pytest.raises(signature_format.PublicKeyError):
        signature_format.load_public_key(str(path))


def test_load_public_key_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        signature_format.load_public_key(str(tmp_path / "nope"))


# ----------------------------------------------------------------------
# check_signatures with a supplied public key
# ----------------------------------------------------------------------

def test_signed_dir_passes_with_correct_key(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    finding = check_signatures(make_ctx(build_dir, public_key=PUB_A))
    assert finding.status is Status.PASS
    for artifact in checks.EXPECTED_ARTIFACTS:
        assert any(artifact in e and "signature verified" in e
                   for e in finding.evidence)
    assert signature_format.keyid_of(PUB_A) in evidence_text(finding)


def test_signed_dir_passes_full_check_run_with_key(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    findings = run_checks(make_ctx(build_dir, public_key=PUB_A))
    assert [f.check for f in findings] == list(checks.CHECK_IDS)
    assert all(f.status is Status.PASS for f in findings)
    report = build_report(make_ctx(build_dir, public_key=PUB_A))
    assert report.ok is True
    assert report.exit_code == 0


def test_no_key_keeps_key_independent_behavior(tmp_path):
    # A plain good dir (zero-byte sig bodies) PASSES without --pub,
    # exactly as before this feature: no key, no cryptographic check.
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    finding = check_signatures(make_ctx(build_dir))
    assert finding.status is Status.PASS
    assert "signature bound" in evidence_text(finding)
    assert "signature verified" not in evidence_text(finding)


def test_correct_keyid_but_other_key_signature_fails(tmp_path):
    # Sign with key B but forge the keyid in the header as key A's,
    # then verify with key A: the keyid binding matches yet the ed25519
    # body does not — this is the case only cryptographic verification
    # catches.
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_B)
    art_path = build_dir / checks.EXPECTED_ARTIFACTS[0]
    sig_path = build_dir / (checks.EXPECTED_ARTIFACTS[0]
                            + checks.SIGNATURE_SUFFIX)
    _, _, body = sig_path.read_bytes().partition(b"\n")
    forged = (f"pengyos-sig-v1 1 ed25519 {signature_format.keyid_of(PUB_A)} "
              f"sha256 {signature_format.sha256_file(str(art_path))}\n"
              ).encode("ascii") + body
    sig_path.write_bytes(forged)

    finding = check_signatures(make_ctx(build_dir, public_key=PUB_A))
    assert finding.status is Status.FAIL
    text = evidence_text(finding)
    assert "ed25519 signature does not verify" in text
    assert str(sig_path) in text


def test_wrong_key_fails_on_keyid(tmp_path):
    # The dir is honestly signed with key A; the verifier supplies key B.
    # The keyid (SHA-256 of A) cannot match B -> keyid FAIL, and the
    # witness must NOT claim it verified any signature.
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    finding = check_signatures(make_ctx(build_dir, public_key=PUB_B))
    assert finding.status is Status.FAIL
    text = evidence_text(finding)
    assert "keyid" in text
    assert "SHA-256 of the supplied public key" in text
    assert signature_format.keyid_of(PUB_B) in text
    assert len(checks.EXPECTED_ARTIFACTS) == \
        text.count("does not match")  # every artifact reported


def test_tampered_sig_body_fails(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    sig_path = build_dir / (checks.EXPECTED_ARTIFACTS[1]
                            + checks.SIGNATURE_SUFFIX)
    raw = bytearray(sig_path.read_bytes())
    raw[-1] ^= 1  # flip one byte of S
    sig_path.write_bytes(bytes(raw))

    finding = check_signatures(make_ctx(build_dir, public_key=PUB_A))
    assert finding.status is Status.FAIL
    assert "ed25519 signature does not verify" in evidence_text(finding)
    assert str(sig_path) in evidence_text(finding)


def test_missing_sig_still_fails_with_key(tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    os.remove(str(build_dir / (checks.EXPECTED_ARTIFACTS[0]
                               + checks.SIGNATURE_SUFFIX)))
    finding = check_signatures(make_ctx(build_dir, public_key=PUB_A))
    assert finding.status is Status.FAIL
    assert "missing" in evidence_text(finding)


# ----------------------------------------------------------------------
# CLI boundary: check --pub FILE
# ----------------------------------------------------------------------

def _write_pub(path, pub) -> str:
    path.write_bytes(pub)
    return str(path)


def test_cli_pub_correct_key_exits_zero(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    pub_file = _write_pub(tmp_path / "pub.bin", PUB_A)

    rc = main(["check", "--build", str(build_dir), "--pub", pub_file])
    assert rc == 0
    out = capsys.readouterr().out
    assert "verdict: PASS" in out
    assert "[PASS] signatures" in out


def test_cli_pub_hex_keyfile_exits_zero(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    pub_file = tmp_path / "pub.hex"
    pub_file.write_text(PUB_A.hex(), encoding="ascii")

    rc = main(["check", "--build", str(build_dir), "--pub", str(pub_file)])
    assert rc == 0
    assert "verdict: PASS" in capsys.readouterr().out


def test_cli_pub_wrong_key_exits_one_with_signatures_finding(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    pub_file = _write_pub(tmp_path / "pub.bin", PUB_B)

    rc = main(["check", "--build", str(build_dir), "--pub", pub_file])
    assert rc == 1
    out = capsys.readouterr().out
    assert "verdict: FAIL" in out
    assert f"[FAIL] {checks.CHECK_SIGNATURES}" in out
    assert "keyid" in out
    # Only the signatures check may fail: the other four are untouched
    # by the supplied key.
    for cid in checks.CHECK_IDS:
        if cid == checks.CHECK_SIGNATURES:
            continue
        assert f"[PASS] {cid}" in out


def test_cli_pub_good_dir_fails_when_key_demanded(capsys, tmp_path):
    # The committed-fixture "good" dir carries zero-byte sig bodies:
    # honest under key-independent verification, but when a key is
    # demanded the witness must call it — its signatures do not verify.
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_good_dir(build_dir)
    pub_file = _write_pub(tmp_path / "pub.bin", PUB_A)

    rc = main(["check", "--build", str(build_dir), "--pub", pub_file])
    assert rc == 1
    assert f"[FAIL] {checks.CHECK_SIGNATURES}" in capsys.readouterr().out


def test_cli_pub_missing_keyfile_exits_two(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)

    rc = main(["check", "--build", str(build_dir),
               "--pub", str(tmp_path / "does-not-exist")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "public key file not found" in err
    # The checks never ran: no report on stdout.
    assert "verdict:" not in capsys.readouterr().out


def test_cli_pub_malformed_keyfile_exits_two(capsys, tmp_path):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"this is not a key\n")

    rc = main(["check", "--build", str(build_dir), "--pub", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "expected 32 raw bytes or 64 hex characters" in err
    assert "verdict:" not in capsys.readouterr().out


def test_cli_json_report_carries_verified_evidence(tmp_path, capsys):
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    build_signed_dir(build_dir, SEED_A)
    pub_file = _write_pub(tmp_path / "pub.bin", PUB_A)
    out = tmp_path / "report.json"

    rc = main(["check", "--build", str(build_dir), "--pub", pub_file,
               "--json", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    sig = next(f for f in data["findings"] if f["check"] == checks.CHECK_SIGNATURES)
    assert sig["status"] == "pass"
    assert any("signature verified" in e for e in sig["evidence"])


def test_parser_pub_flag_defaults_none():
    args = build_parser().parse_args(["check", "--build", "d"])
    assert args.pub_key is None
    args = build_parser().parse_args(
        ["check", "--build", "d", "--pub", "k.bin"])
    assert args.pub_key == "k.bin"
