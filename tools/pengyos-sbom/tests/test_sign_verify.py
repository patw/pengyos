"""Tests for the ``sign`` / ``verify`` CLI subcommands (subprocess level).

End-to-end contract exercised against the committed fixtures:

* ``generate`` → ``sign`` → ``verify`` round-trip (SPDX and CycloneDX);
* tampered document, tampered signature, wrong key → non-zero exit;
* default ``FILE.sig`` sidecar path and explicit ``--out`` / ``--sig``;
* ``sign`` refuses invalid documents; ``verify`` reports a valid signature
  but a structurally invalid document as a failure;
* missing SBOM / key / signature files → clear error, exit 1.

The test keypair is the RFC 8032 TEST 1 key, written to temp files (raw
and base64 forms); nothing external is required.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
LEGAL = FIX / "legal-info"
SHOWINFO = FIX / "show-info.json"

from pengyos_sbom import cli, constants, ed25519  # noqa: E402

SEED = bytes.fromhex(
    "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
)
PUB = ed25519.publickey(SEED)


def run_cli(*args, cwd=REPO):
    return subprocess.run(
        [sys.executable, "-m", "pengyos_sbom", *map(str, args)],
        cwd=cwd,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def keys(tmp_path):
    """Write the fixed keypair as raw + base64 files; return their paths."""
    seed = tmp_path / "seed.key"
    seed.write_bytes(SEED)
    seed_b64 = tmp_path / "seed.b64.key"
    seed_b64.write_text(base64.b64encode(SEED).decode() + "\n")
    pub = tmp_path / "pub.key"
    pub.write_bytes(PUB)
    pub_b64 = tmp_path / "pub.b64.key"
    pub_b64.write_text(base64.b64encode(PUB).decode() + "\n")
    return {"seed": seed, "seed_b64": seed_b64, "pub": pub, "pub_b64": pub_b64}


@pytest.fixture
def sbom(tmp_path):
    """Generate a real SPDX SBOM from the fixtures; return its path."""
    out = tmp_path / "sbom.spdx.json"
    res = run_cli(
        "generate", "--legal-info", LEGAL, "--show-info", SHOWINFO, "--out", out
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    return out


@pytest.fixture
def cdx_sbom(tmp_path):
    """Generate a real CycloneDX BOM from the fixtures; return its path."""
    out = tmp_path / "bom.cdx.json"
    res = run_cli(
        "generate",
        "--legal-info", LEGAL,
        "--show-info", SHOWINFO,
        "--out", tmp_path / "unused.spdx.json",
        "--cdx", out,
    )
    assert res.returncode == constants.EXIT_OK, res.stderr
    return out


# --- generate -> sign -> verify round trip -------------------------------
def test_sign_verify_spdx_roundtrip(tmp_path, sbom, keys):
    res = run_cli("sign", sbom, "--key", keys["seed"])
    assert res.returncode == constants.EXIT_OK, res.stderr
    sig = sbom.parent / (sbom.name + ".sig")
    assert sig.exists(), "default sidecar FILE.sig missing"
    assert sig.stat().st_size == ed25519.SIGNATURE_LEN
    assert f"wrote ed25519 signature" in res.stdout

    res = run_cli("verify", sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert "signature OK" in res.stdout
    assert "OK (verified SPDX 2.3 SBOM)" in res.stdout


def test_sign_verify_cyclonedx_roundtrip(tmp_path, cdx_sbom, keys):
    res = run_cli("sign", cdx_sbom, "--key", keys["seed"])
    assert res.returncode == constants.EXIT_OK, res.stderr
    res = run_cli("verify", cdx_sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert "OK (verified CycloneDX 1.5 SBOM)" in res.stdout


def test_sign_with_base64_key_and_verify_with_seed_file(tmp_path, sbom, keys):
    # base64 key file for signing...
    res = run_cli("sign", sbom, "--key", keys["seed_b64"])
    assert res.returncode == constants.EXIT_OK, res.stderr
    # ...and the *signing* (seed) file for verification (seed fallback).
    res = run_cli("verify", sbom, "--key", keys["seed"])
    assert res.returncode == constants.EXIT_OK, res.stderr


def test_sign_explicit_out_and_verify_explicit_sig(tmp_path, sbom, keys):
    sig = tmp_path / "elsewhere" / "my.sig"
    res = run_cli("sign", sbom, "--key", keys["seed"], "--out", sig)
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert sig.exists() and not (sbom.parent / (sbom.name + ".sig")).exists()
    res = run_cli("verify", sbom, "--key", keys["pub_b64"], "--sig", sig)
    assert res.returncode == constants.EXIT_OK, res.stderr


def test_signature_is_deterministic(tmp_path, sbom, keys):
    res1 = run_cli("sign", sbom, "--key", keys["seed"], "--out", tmp_path / "a.sig")
    res2 = run_cli("sign", sbom, "--key", keys["seed"], "--out", tmp_path / "b.sig")
    assert res1.returncode == res2.returncode == constants.EXIT_OK
    assert (tmp_path / "a.sig").read_bytes() == (tmp_path / "b.sig").read_bytes()


# --- Tamper detection ------------------------------------------------------
def test_tampered_document_fails_verify(tmp_path, sbom, keys):
    assert run_cli("sign", sbom, "--key", keys["seed"]).returncode == 0
    doc = json.loads(sbom.read_text())
    doc["packages"][0]["name"] = "evil-fork-of-" + doc["packages"][0]["name"]
    sbom.write_text(json.dumps(doc))
    res = run_cli("verify", sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_ERROR
    assert "signature INVALID" in res.stderr


def test_trailing_whitespace_is_normalized_not_detected(tmp_path, sbom, keys):
    # A serialization-level change (trailing whitespace) is normalized away by
    # the canonicalization seam, so the signature still verifies.
    assert run_cli("sign", sbom, "--key", keys["seed"]).returncode == 0
    with sbom.open("ab") as f:
        f.write(b" ")
    res = run_cli("verify", sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert "signature OK" in res.stdout


def test_content_tamper_still_fails_after_normalization(tmp_path, sbom, keys):
    # A real content change (not just whitespace) is still detected once the
    # timestamp/whitespace normalization is in place.
    assert run_cli("sign", sbom, "--key", keys["seed"]).returncode == 0
    doc = json.loads(sbom.read_text())
    for pkg in doc["packages"]:
        if "versionInfo" in pkg:
            pkg["versionInfo"] = "0.0.0-evil"
            break
    else:
        doc["packages"][0]["name"] = "evil-fork"
    sbom.write_text(json.dumps(doc))
    res = run_cli("verify", sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_ERROR
    assert "signature INVALID" in res.stderr


def test_signature_is_independent_of_creation_timestamp(tmp_path, keys):
    # The headline "deterministic output" property: a signature produced over a
    # document with one creation timestamp still verifies a copy of the same
    # document whose timestamp was changed (normalized out of the hashed body).
    from pengyos_sbom.ingest import ingest
    from pengyos_sbom.spdx import build_spdx_document

    result = ingest(LEGAL, SHOWINFO)
    doc_a = build_spdx_document(result, created="2026-01-01T00:00:00+00:00")
    doc_b = build_spdx_document(result, created="2027-06-15T12:34:56+00:00")

    a = tmp_path / "a.spdx.json"
    b = tmp_path / "b.spdx.json"
    a.write_text(json.dumps(doc_a, indent=2, sort_keys=True) + "\n")
    b.write_text(json.dumps(doc_b, indent=2, sort_keys=True) + "\n")
    assert a.read_text() != b.read_text()  # the raw bytes genuinely differ

    # sign file A...
    sig = run_cli("sign", a, "--key", keys["seed"], "--out", tmp_path / "a.sig")
    assert sig.returncode == constants.EXIT_OK, sig.stderr
    # ...and verify file B (different timestamp) with that same signature.
    res = run_cli("verify", b, "--key", keys["pub"], "--sig", tmp_path / "a.sig")
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert "signature OK" in res.stdout


def test_cyclonedx_signature_is_independent_of_timestamp(tmp_path, cdx_sbom, keys):
    # Same property for the CycloneDX format (metadata.timestamp).
    assert run_cli("sign", cdx_sbom, "--key", keys["seed"]).returncode == 0
    doc = json.loads(cdx_sbom.read_text())
    doc["metadata"]["timestamp"] = "2027-06-15T12:34:56+00:00"
    cdx_sbom.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    res = run_cli("verify", cdx_sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_OK, res.stderr
    assert "signature OK" in res.stdout


def test_tampered_signature_fails_verify(tmp_path, sbom, keys):
    assert run_cli("sign", sbom, "--key", keys["seed"]).returncode == 0
    sig = sbom.parent / (sbom.name + ".sig")
    data = bytearray(sig.read_bytes())
    data[10] ^= 1
    sig.write_bytes(bytes(data))
    res = run_cli("verify", sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_ERROR
    assert "signature INVALID" in res.stderr


def test_wrong_key_fails_verify(tmp_path, sbom, keys):
    assert run_cli("sign", sbom, "--key", keys["seed"]).returncode == 0
    other_pub = tmp_path / "other.pub"
    other_pub.write_bytes(ed25519.publickey(bytes(32)))
    res = run_cli("verify", sbom, "--key", other_pub)
    assert res.returncode == constants.EXIT_ERROR
    assert "signature INVALID" in res.stderr


def test_signature_from_other_sbom_fails(tmp_path, sbom, cdx_sbom, keys):
    # A signature for one document must not verify a different one.
    assert run_cli("sign", sbom, "--key", keys["seed"]).returncode == 0
    sig = sbom.parent / (sbom.name + ".sig")
    res = run_cli("verify", cdx_sbom, "--key", keys["pub"], "--sig", sig)
    assert res.returncode == constants.EXIT_ERROR
    assert "signature INVALID" in res.stderr


# --- Refusal / structural checks ------------------------------------------
def test_sign_refuses_invalid_document(tmp_path, keys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"spdxVersion": "SPDX-2.3"}')  # missing required fields
    res = run_cli("sign", bad, "--key", keys["seed"])
    assert res.returncode == constants.EXIT_ERROR
    assert "refusing to sign" in res.stderr
    assert not (tmp_path / "bad.json.sig").exists()


def test_sign_refuses_non_sbom_json(tmp_path, keys):
    other = tmp_path / "other.json"
    other.write_text('{"hello": "world"}')
    res = run_cli("sign", other, "--key", keys["seed"])
    assert res.returncode == constants.EXIT_ERROR
    assert "refusing to sign" in res.stderr


def test_verify_signature_ok_but_document_invalid(tmp_path, sbom, keys):
    # Sign the real SBOM, then *replace* its contents with a same-length-ish
    # invalid document: the old signature can no longer match either, so the
    # documented behavior is signature INVALID plus structural problems.
    assert run_cli("sign", sbom, "--key", keys["seed"]).returncode == 0
    sbom.write_text('{"spdxVersion": "SPDX-2.3"}')
    res = run_cli("verify", sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_ERROR
    assert "signature INVALID" in res.stderr
    assert "problem(s) found" in res.stderr


# --- Error handling ---------------------------------------------------------
def test_sign_missing_sbom(tmp_path, keys):
    res = run_cli("sign", tmp_path / "nope.json", "--key", keys["seed"])
    assert res.returncode == constants.EXIT_ERROR
    assert "no such file" in res.stderr


def test_sign_missing_key(sbom, tmp_path):
    res = run_cli("sign", sbom, "--key", tmp_path / "nokey.key")
    assert res.returncode == constants.EXIT_ERROR
    assert "No such file or directory" in res.stderr


def test_sign_bad_key_format(sbom, tmp_path):
    key = tmp_path / "bad.key"
    key.write_text("not a key at all\n")
    res = run_cli("sign", sbom, "--key", key)
    assert res.returncode == constants.EXIT_ERROR
    assert "not 32 raw bytes and not a valid base64 key" in res.stderr


def test_verify_missing_signature(sbom, keys):
    res = run_cli("verify", sbom, "--key", keys["pub"])
    assert res.returncode == constants.EXIT_ERROR
    assert "no such file" in res.stderr


def test_verify_missing_sbom(tmp_path, keys):
    sig = tmp_path / "a.sig"
    sig.write_bytes(bytes(64))
    res = run_cli("verify", tmp_path / "nope.json", "--key", keys["pub"], "--sig", sig)
    assert res.returncode == constants.EXIT_ERROR
    assert "no such file" in res.stderr


def test_verify_missing_key(sbom, tmp_path):
    res = run_cli("verify", sbom, "--key", tmp_path / "nokey.pub")
    assert res.returncode == constants.EXIT_ERROR
    assert "No such file or directory" in res.stderr


# --- In-process dispatch ----------------------------------------------------
def test_main_dispatch_sign_verify_no_not_implemented(tmp_path, sbom, keys):
    # Regression: after implementing sign/verify, no subcommand may fall
    # through to the (removed) scaffold path (exit 3).
    rc = cli.main(["sign", str(sbom), "--key", str(keys["seed"])])
    assert rc == constants.EXIT_OK
    rc = cli.main(["verify", str(sbom), "--key", str(keys["pub"])])
    assert rc == constants.EXIT_OK
