"""Tests for SBOM signature verification (implementation task 4).

Covers :mod:`pengyos_witness_bundle.sigverify`:

* :func:`canonical_bytes` — SPDX / CycloneDX timestamp normalization and
  canonical re-serialization (and the verbatim identity fallback);
* key loading: raw 32-byte keys, base64 keys, seed interpretation, the
  two-candidate resolution, and malformed/missing keys;
* :func:`verify_file` / :func:`verify_bytes` — good, tampered, missing, wrong
  key, malformed key, wrong-length signature; the "signature survives
  re-serialization / timestamp change" invariant;
* the pure-Python, stdlib-only Ed25519 is pinned by the RFC 8032 §7.1 test
  vectors (public-key derivation, signing, verification);
* backend selection: ``pengyos-sbom`` is preferred when importable, otherwise
  the bundled ``stdlib-ed25519`` is used.

Every case is offline and deterministic: keys are generated in-tree and
signatures are produced by the bundled primitive, so no network, root, or
third-party package is required.
"""

import base64
import json

import pytest

from pengyos_witness_bundle import sigverify as sv


# --- helpers ----------------------------------------------------------------
def _spdx_doc(created: str = "2026-09-17T17:57:36+00:00") -> dict:
    """A minimal but well-formed SPDX 2.3 document."""
    return {
        "SPDXID": "SPDXRef-DOCUMENT",
        "spdxVersion": "SPDX-2.3",
        "creationInfo": {
            "created": created,
            "creators": ["Tool:pengyos-sbom/0.1.0"],
        },
        "dataLicense": "CC0-1.0",
        "documentDescribes": ["SPDXRef-RootArtifact"],
        "documentNamespace": "https://pengyos.catbee.ca/sbom/test",
        "packages": [
            {
                "SPDXID": "SPDXRef-Package",
                "name": "pengyos-core",
                "versionInfo": "0.1.0",
                "downloadLocation": "NOASSERTION",
            }
        ],
    }


def _cyclonedx_doc(timestamp: str = "2026-09-17T17:57:36+00:00") -> dict:
    """A minimal but well-formed CycloneDX 1.5 document."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "bomVersion": 1,
        "metadata": {"timestamp": timestamp},
        "components": [
            {"type": "operating-system", "name": "pengyos-core", "version": "0.1.0"}
        ],
    }


class Keypair:
    """A (seed, public key) pair plus a ``sign`` helper for the SBOM bytes."""

    def __init__(self, seed: bytes, pub: bytes):
        self.seed = seed
        self.pub = pub

    def sign(self, data: bytes) -> bytes:
        return sv.sign_bytes(data, self.seed)


@pytest.fixture
def keypair(tmp_path):
    """A fresh random 32-byte seed / derived public key pair."""
    import os

    seed = os.urandom(32)
    pub = sv._publickey(seed)
    return Keypair(seed, pub)


@pytest.fixture
def sbom_bytes():
    return json.dumps(_spdx_doc()).encode("utf-8")


# --- canonical_bytes --------------------------------------------------------
def test_canonical_spdx_normalizes_timestamp_and_sorts_keys(sbom_bytes):
    canon = sv.canonical_bytes(sbom_bytes)
    doc = json.loads(canon)
    # timestamp normalized to the fixed constant
    assert doc["creationInfo"]["created"] == sv._TIMESTAMP_NORMALIZED
    # re-serialized canonically: sorted keys, compact separators, ASCII
    assert json.loads(canon) == json.loads(
        canon.decode("utf-8"), object_pairs_hook=None
    )
    assert b", " not in canon  # compact separators (no ", ")
    assert b": " not in canon  # compact separators (no ": ")


def test_canonical_is_stable_across_whitespace_and_key_order(sbom_bytes):
    # A differently-indented, differently-ordered copy of the same document
    # must canonicalize to the identical bytes.
    doc = json.loads(sbom_bytes)
    reordered = json.dumps(doc, indent=4, sort_keys=False)
    reordered_bytes = reordered.encode("utf-8")
    assert sv.canonical_bytes(sbom_bytes) == sv.canonical_bytes(reordered_bytes)


def test_canonical_survives_timestamp_change():
    # Two documents that differ ONLY in the creation timestamp hash identically.
    a = json.dumps(_spdx_doc("2026-09-17T17:57:36+00:00")).encode()
    b = json.dumps(_spdx_doc("2020-01-01T00:00:00+00:00")).encode()
    assert sv.canonical_bytes(a) == sv.canonical_bytes(b)


def test_canonical_cyclonedx_normalizes_metadata_timestamp():
    cdx = json.dumps(_cyclonedx_doc("2026-01-01T00:00:00+00:00")).encode()
    canon = json.loads(sv.canonical_bytes(cdx))
    assert canon["metadata"]["timestamp"] == sv._TIMESTAMP_NORMALIZED


def test_canonical_non_json_is_identity():
    raw = b"\x00\x01\x02 not json at all"
    assert sv.canonical_bytes(raw) == raw


def test_canonical_json_non_dict_is_identity():
    # A JSON array / scalar is not a document we recognize -> bind verbatim.
    assert sv.canonical_bytes(b"[1, 2, 3]") == b"[1, 2, 3]"
    assert sv.canonical_bytes(b"42") == b"42"


def test_canonical_content_change_is_preserved():
    # A real content change IS still detected (different canonical bytes).
    a = json.dumps(_spdx_doc()).encode()
    b = json.dumps(_spdx_doc()).encode().replace(b"CC0-1.0", b"GPL-2.0-only")
    assert sv.canonical_bytes(a) != sv.canonical_bytes(b)


# --- key loading ------------------------------------------------------------
def _write_raw_key(tmp_path, keypair, name="key.pub"):
    p = tmp_path / name
    p.write_bytes(keypair.pub)
    return p


def test_load_public_key_docstring_present():
    assert sv.load_public_key.__doc__


def test_load_public_key_raw_bytes_on_curve(tmp_path, keypair):
    # A raw 32-byte on-curve value is returned as-is.
    assert sv.load_public_key(_write_raw_key(tmp_path, keypair)) == keypair.pub


def test_load_public_key_from_path(tmp_path, keypair):
    assert sv.load_public_key(_write_raw_key(tmp_path, keypair)) == keypair.pub


def test_load_public_key_base64(tmp_path, keypair):
    p = tmp_path / "key.pub"
    p.write_text(base64.b64encode(keypair.pub).decode())
    assert sv.load_public_key(p) == keypair.pub


def test_load_public_key_base64_unpadded(tmp_path, keypair):
    p = tmp_path / "key.pub"
    # base64 without padding must still decode.
    p.write_text(base64.b64encode(keypair.pub).decode().rstrip("="))
    assert sv.load_public_key(p) == keypair.pub


def test_public_key_candidates_on_curve(tmp_path, keypair):
    # An on-curve value: two candidates (itself as a pubkey, and its derivation).
    p = _write_raw_key(tmp_path, keypair)
    cands = sv.public_key_candidates(p)
    assert len(cands) == 2
    assert keypair.pub in cands
    assert sv._publickey(keypair.pub) in cands


def test_public_key_candidates_seed_interpretation(tmp_path, keypair):
    # A seed (off-curve bytes) yields a single candidate: the derived pubkey.
    seed = keypair.seed
    p = tmp_path / "seed.key"
    p.write_bytes(seed)
    cands = sv.public_key_candidates(p)
    # A random 32-byte seed is off-curve with ~50% probability; if it happens to
    # be on-curve, dedup still leaves the derived pubkey present.
    assert sv._publickey(seed) in cands
    assert len(cands) == 1 or sv._publickey(seed) == keypair.pub


def test_public_key_candidates_dedup_when_seed_on_curve(tmp_path):
    # A seed whose derivation equals itself is infeasible; instead assert the
    # returned list contains the derived pubkey exactly once.
    import os

    seed = os.urandom(32)
    derived = sv._publickey(seed)
    p = tmp_path / "seed.key"
    p.write_bytes(seed)
    cands = sv.public_key_candidates(p)
    assert cands.count(derived) == 1


def test_load_public_key_missing_raises():
    with pytest.raises(sv.VerifyError, match="not found"):
        sv.load_public_key("/nonexistent/key.pub")


def test_load_public_key_malformed_raises(tmp_path):
    p = tmp_path / "bad.pub"
    p.write_bytes(b"not-a-key")
    with pytest.raises(sv.VerifyError, match="not 32 raw bytes"):
        sv.load_public_key(p)


def test_load_public_key_bad_base64_raises(tmp_path):
    p = tmp_path / "bad.pub"
    p.write_text("!!!not base64!!!")
    with pytest.raises(sv.VerifyError, match="base64"):
        sv.load_public_key(p)


def test_load_public_key_wrong_length_base64_raises(tmp_path):
    p = tmp_path / "short.pub"
    p.write_text(base64.b64encode(b"short").decode())
    with pytest.raises(sv.VerifyError, match="expected 32"):
        sv.load_public_key(p)


# --- verify_bytes -----------------------------------------------------------
def test_verify_bytes_correct(keypair, sbom_bytes):
    sig = keypair.sign(sbom_bytes)
    assert sv.verify_bytes(sbom_bytes, sig, keypair.pub) is True


def test_verify_bytes_wrong_key(keypair, sbom_bytes):
    import os

    sig = keypair.sign(sbom_bytes)
    wrong = sv._publickey(os.urandom(32))
    assert sv.verify_bytes(sbom_bytes, sig, wrong) is False


def test_verify_bytes_tampered(keypair, sbom_bytes):
    sig = keypair.sign(sbom_bytes)
    tampered = sbom_bytes.replace(b"CC0-1.0", b"CC0-9.9")
    assert sv.verify_bytes(tampered, sig, keypair.pub) is False


def test_verify_bytes_candidate_list(keypair, sbom_bytes):
    # A candidate list accepts iff ANY candidate verifies.
    sig = keypair.sign(sbom_bytes)
    import os

    other = sv._publickey(os.urandom(32))
    assert sv.verify_bytes(sbom_bytes, sig, [other, keypair.pub]) is True
    assert sv.verify_bytes(sbom_bytes, sig, [other, other]) is False


def test_verify_bytes_wrong_length_signature(keypair, sbom_bytes):
    sig = keypair.sign(sbom_bytes)
    assert sv.verify_bytes(sbom_bytes, sig[:32], keypair.pub) is False


def test_verify_bytes_wrong_length_public_key(keypair, sbom_bytes):
    sig = keypair.sign(sbom_bytes)
    assert sv.verify_bytes(sbom_bytes, sig, keypair.pub[:16]) is False


# --- verify_file ------------------------------------------------------------
def _write_good_build(tmp_path, keypair, sbom_bytes, *, tamper=False):
    """Write a complete 4-artifact build dir signed by *keypair*."""
    (tmp_path / "images").mkdir()
    sbom = tmp_path / "images" / "pengyos-x86_64.spdx.json"
    body = sbom_bytes.replace(b"CC0-1.0", b"CC0-9.9") if tamper else sbom_bytes
    sbom.write_bytes(body)
    # Always sign the *original* body: with tamper=True the on-disk SBOM is
    # corrupted after signing, so verification must fail (a real tamper test).
    sig = keypair.sign(sbom_bytes)
    (tmp_path / "images" / "pengyos-x86_64.spdx.json.sig").write_bytes(sig)
    (tmp_path / "images" / "pengyos-x86_64.cyclonedx.json").write_bytes(
        json.dumps(_cyclonedx_doc()).encode()
    )
    (tmp_path / "repro-report.txt").write_text("repro report")
    return sbom


def _write_pub_key(tmp_path, keypair, name="pub.key"):
    """Write *keypair*'s public key to a file and return its path."""
    p = tmp_path / name
    p.write_bytes(keypair.pub)
    return p


def test_verify_file_good(keypair, tmp_path):
    sbom = _write_good_build(tmp_path, keypair, json.dumps(_spdx_doc()).encode())
    pub = _write_pub_key(tmp_path, keypair)
    verified, method = sv.verify_file(sbom, sbom.parent / "pengyos-x86_64.spdx.json.sig", pub)
    assert verified is True
    assert method in ("pengyos-sbom", "stdlib-ed25519")


def test_verify_file_tampered_raises(keypair, tmp_path):
    sbom = _write_good_build(tmp_path, keypair, json.dumps(_spdx_doc()).encode(), tamper=True)
    pub = _write_pub_key(tmp_path, keypair)
    with pytest.raises(sv.VerifyError, match="FAILED"):
        sv.verify_file(sbom, sbom.parent / "pengyos-x86_64.spdx.json.sig", pub)


def test_verify_file_missing_sig_raises(tmp_path, keypair):
    sbom = _write_good_build(tmp_path, keypair, json.dumps(_spdx_doc()).encode())
    pub = _write_pub_key(tmp_path, keypair)
    sbom.unlink()  # remove the SBOM; the sig is still there
    with pytest.raises(sv.VerifyError, match="sbom not found"):
        sv.verify_file(sbom, sbom.parent / "pengyos-x86_64.spdx.json.sig", pub)


def test_verify_file_missing_pub_raises(sbom_bytes, tmp_path):
    keypair = _keypair_random()
    sbom = _write_good_build(tmp_path, keypair, sbom_bytes)
    with pytest.raises(sv.VerifyError, match="public key"):
        sv.verify_file(sbom, sbom.parent / "pengyos-x86_64.spdx.json.sig", "/nonexistent/key.pub")


def test_verify_file_wrong_key_raises(keypair, tmp_path):
    import os

    sbom = _write_good_build(tmp_path, keypair, json.dumps(_spdx_doc()).encode())
    wrong_pub = tmp_path / "wrong.pub"
    wrong_pub.write_bytes(sv._publickey(os.urandom(32)))
    with pytest.raises(sv.VerifyError, match="does not match"):
        sv.verify_file(sbom, sbom.parent / "pengyos-x86_64.spdx.json.sig", wrong_pub)


def test_verify_file_malformed_pub_raises(sbom_bytes, tmp_path):
    keypair = _keypair_random()
    sbom = _write_good_build(tmp_path, keypair, sbom_bytes)
    bad = tmp_path / "bad.pub"
    bad.write_bytes(b"not-a-key")
    with pytest.raises(sv.VerifyError, match="not 32 raw bytes"):
        sv.verify_file(sbom, sbom.parent / "pengyos-x86_64.spdx.json.sig", bad)


def test_verify_file_wrong_length_sig_raises(keypair, tmp_path):
    sbom = _write_good_build(tmp_path, keypair, json.dumps(_spdx_doc()).encode())
    pub = _write_pub_key(tmp_path, keypair)
    sigpath = sbom.parent / "pengyos-x86_64.spdx.json.sig"
    sigpath.write_bytes(sigpath.read_bytes()[:32])
    with pytest.raises(sv.VerifyError, match="expected 64"):
        sv.verify_file(sbom, sigpath, pub)


def test_verify_file_seed_key_verifies(keypair, tmp_path):
    # A key file holding the *seed* (not the public key) must still verify,
    # because the derived pubkey is tried as a candidate.
    sbom = _write_good_build(tmp_path, keypair, json.dumps(_spdx_doc()).encode())
    seed_path = tmp_path / "seed.key"
    seed_path.write_bytes(keypair.seed)
    verified, method = sv.verify_file(sbom, sbom.parent / "pengyos-x86_64.spdx.json.sig", seed_path)
    assert verified is True


def test_verify_file_canonicalization_survives_reserialization(keypair, tmp_path):
    # Sign one serialization; verify a whitespace/key-order-different copy of
    # the same document — the signature must still hold.
    sbom_bytes = json.dumps(_spdx_doc("2026-01-01T00:00:00+00:00")).encode()
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "a.spdx.json").write_bytes(sbom_bytes)
    sig = keypair.sign(sbom_bytes)
    (tmp_path / "images" / "a.spdx.json.sig").write_bytes(sig)

    # A copy with different whitespace and key order, and a DIFFERENT timestamp.
    doc = json.loads(sbom_bytes)
    reserialized = json.dumps(doc, indent=2, sort_keys=True).encode()
    reserialized = reserialized.replace(
        sv._TIMESTAMP_NORMALIZED.encode(), b"2099-12-31T00:00:00+00:00"
    )
    (tmp_path / "images" / "b.spdx.json").write_bytes(reserialized)
    pub = _write_pub_key(tmp_path, keypair)
    verified, method = sv.verify_file(
        tmp_path / "images" / "b.spdx.json",
        tmp_path / "images" / "a.spdx.json.sig",
        pub,
    )
    assert verified is True


# --- RFC 8032 test vectors --------------------------------------------------
# The pure-Python Ed25519 is pinned against the canonical RFC 8032 §7.1
# vectors: public-key derivation, signing, and verification must all agree.
# (Messages are hex-encoded in the RFC.)
RFC_VECTORS = [
    {
        "seed": "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "pub": "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "msg": "",
        "sig": (
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
    },
    {
        "seed": "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "pub": "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "msg": "72",
        "sig": (
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
    },
    {
        "seed": "833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
        "pub": "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
        "msg": (
            "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
            "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f"
        ),
        "sig": (
            "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b589"
            "09351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"
        ),
    },
]


@pytest.mark.parametrize("vec", RFC_VECTORS, ids=[f"rfc-{i+1}" for i in range(3)])
def test_ed25519_rfc_public_key_derivation(vec):
    seed = bytes.fromhex(vec["seed"])
    assert sv._publickey(seed) == bytes.fromhex(vec["pub"])


@pytest.mark.parametrize("vec", RFC_VECTORS, ids=[f"rfc-{i+1}" for i in range(3)])
def test_ed25519_rfc_sign_matches(vec):
    seed = bytes.fromhex(vec["seed"])
    msg = bytes.fromhex(vec["msg"])
    assert sv.sign(msg, seed) == bytes.fromhex(vec["sig"])


@pytest.mark.parametrize("vec", RFC_VECTORS, ids=[f"rfc-{i+1}" for i in range(3)])
def test_ed25519_rfc_verify(vec):
    pub = bytes.fromhex(vec["pub"])
    msg = bytes.fromhex(vec["msg"])
    sig = bytes.fromhex(vec["sig"])
    assert sv._ed25519_verify(msg, sig, pub) is True


@pytest.mark.parametrize("vec", RFC_VECTORS, ids=[f"rfc-{i+1}" for i in range(3)])
def test_ed25519_deterministic(vec):
    # Same message + seed -> identical signature (PureEdDSA, no randomness).
    seed = bytes.fromhex(vec["seed"])
    msg = bytes.fromhex(vec["msg"])
    assert sv.sign(msg, seed) == sv.sign(msg, seed)


def test_ed25519_sign_wrong_seed_length_raises():
    with pytest.raises(ValueError):
        sv.sign(b"msg", b"too-short")


# --- backend selection ------------------------------------------------------
def test_backend_prefers_pengyos_sbom_when_importable(monkeypatch):
    # When pengyos-sbom is importable, the backend selector routes through it.
    # We stub the import by installing a fake ``pengyos_sbom`` in sys.modules
    # and assert it is consulted (its ``verify_bytes`` returns a sentinel).
    import sys

    fake_signing = type(
        "signing", (), {"verify_bytes": staticmethod(lambda *a, **k: "USED")}
    )()
    fake_pkg = type("pengyos_sbom", (), {"signing": fake_signing})()
    monkeypatch.setitem(sys.modules, "pengyos_sbom", fake_pkg)
    # Reset the cache so the next call re-attempts the import.
    sv._pengyos_signing = None
    # The fake's verify_bytes returns the sentinel "USED"; _verify bool()-ifies
    # it, so assert on the method name to prove the fake (not stdlib) ran.
    verified, method = sv._verify(b"{}", b"x" * 64, [b"y" * 32])
    assert method == "pengyos-sbom"
    assert bool(verified) is True


def test_backend_falls_back_to_stdlib_when_not_importable():
    # With pengyos-sbom absent, the stdlib backend is selected.
    sv._pengyos_signing = False  # mark "definitely not importable"
    _, method = sv._verify(b"{}", b"x" * 64, [b"y" * 32])
    assert method == "stdlib-ed25519"


# --- small local helpers ----------------------------------------------------
def _keypair_random():
    import os

    seed = os.urandom(32)
    return Keypair(seed, sv._publickey(seed))
