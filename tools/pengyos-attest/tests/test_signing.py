"""Tests for head signing.

Covers:

* the pinned ``head_message`` canonical format,
* ``keygen`` (seed 0600 / public key 0644, derived key, never overwrites),
* key-file loading (raw 32 bytes, 64-lowercase-hex text; the
  verification-key candidate semantics — a file that signed can verify),
* signature entries (fields, determinism, self-verification, every tamper
  variant rejected),
* ``chain.sign_head`` (sidecar append, duplicate refusal, corrupt/empty
  chain refusal),
* ``verify_chain(public_key=...)`` (head coverage, wrong key, stale head,
  truncation of a signed chain, signature-log tamper detection even without
  a key, chain findings reported before signature findings),
* the ``sign`` / ``keygen`` / ``verify --public-key`` CLI subcommands
  end-to-end (exit codes 0/1/2).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from pengyos_attest import chain, ed25519, signing
from pengyos_attest.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, main
from pengyos_attest.record import to_json_line

SEED = bytes(range(32))
PUB = ed25519.publickey(SEED)
OTHER_SEED = bytes([0xAB] * 32)
OTHER_PUB = ed25519.publickey(OTHER_SEED)


def _make_chain(tmp_path: Path, n: int = 3, name: str = "chain.jsonl") -> list[dict]:
    log = tmp_path / name
    records = []
    for i in range(n):
        records.append(chain.append_event(
            log, "build", {"i": i}, ts=f"2026-01-01T00:00:0{i}Z"))
    return records


def _write_keyfile(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# --- head message (the one and only signed-byte definition) ---------------


def test_head_message_exact_bytes():
    assert signing.head_message(1, "ab" * 32) == (
        b"pengyos-attest/head-v1\nseq=1\nhash=" + b"ab" * 32 + b"\n"
    )


def test_head_message_depends_on_seq_and_hash():
    base = signing.head_message(3, "cd" * 32)
    assert signing.head_message(4, "cd" * 32) != base
    assert signing.head_message(3, "ce" * 32) != base
    assert signing.HEAD_MESSAGE_VERSION == 1


# --- keygen -----------------------------------------------------------------


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_keygen_writes_seed_and_pub(tmp_path):
    seed, pub, seed_path, pub_path = signing.generate_keypair(tmp_path / "attest.key")
    assert seed_path == tmp_path / "attest.key"
    assert pub_path == tmp_path / "attest.key.pub"
    assert seed_path.read_bytes() == seed
    assert pub_path.read_bytes() == pub
    assert ed25519.publickey(seed) == pub
    assert len(seed) == 32
    assert _mode(seed_path) == 0o600
    assert _mode(pub_path) == 0o644


def test_keygen_default_pub_path_variants(tmp_path):
    seed, pub, seed_path, pub_path = signing.generate_keypair(tmp_path / "key")
    assert pub_path == tmp_path / "key.pub"
    # A path already ending in .pub: the .pub suffix is REPLACED.
    assert signing.default_pub_path(tmp_path / "a.pub") == tmp_path / "a"
    # The seed file that signs can be read back as a signing key.
    assert signing.load_key_bytes(seed_path) == seed


def test_keygen_never_overwrites(tmp_path):
    seed, _, seed_path, pub_path = signing.generate_keypair(tmp_path / "k.key")
    with pytest.raises(signing.KeygenError, match="refusing to overwrite"):
        signing.generate_keypair(tmp_path / "k.key")
    # Existing PUB file also blocks.
    (tmp_path / "k2.key").write_bytes(bytes(32))
    with pytest.raises(signing.KeygenError, match="refusing to overwrite"):
        signing.generate_keypair(tmp_path / "k2.key")
    assert not (tmp_path / "k2.key.pub").exists()
    # The original pair is untouched.
    assert seed_path.read_bytes() == seed
    assert pub_path.read_bytes() == ed25519.publickey(seed)


def test_keygen_creates_parent_dirs(tmp_path):
    seed, pub, seed_path, pub_path = signing.generate_keypair(
        tmp_path / "nested" / "deep" / "k.key")
    assert seed_path.exists() and pub_path.exists()
    assert seed != ed25519.publickey(seed)  # sanity: a seed is not its pubkey


def test_keygen_produces_distinct_seeds(tmp_path):
    s1 = signing.generate_keypair(tmp_path / "a.key")[0]
    s2 = signing.generate_keypair(tmp_path / "b.key")[0]
    assert s1 != s2


# --- key file loading -------------------------------------------------------


def test_load_key_bytes_raw(tmp_path):
    assert signing.load_key_bytes(_write_keyfile(tmp_path / "raw", SEED)) == SEED


def test_load_key_bytes_hex_text_with_whitespace(tmp_path):
    p = _write_keyfile(tmp_path / "hex", (PUB.hex() + "\n").encode("ascii"))
    assert signing.load_key_bytes(p) == PUB
    p2 = _write_keyfile(tmp_path / "hex2", b"  " + SEED.hex().encode() + b" \n")
    assert signing.load_key_bytes(p2) == SEED


@pytest.mark.parametrize(
    "content,why",
    [
        (b"", "empty"),
        (b"\x00" * 31, "31 raw bytes"),
        (b"\x00" * 33, "33 raw bytes"),
        (b"zz" * 32, "not hex"),
        (PUB.hex().encode()[:63], "63 hex chars"),
        (PUB.hex().upper().encode(), "UPPERCASE hex must be rejected"),
        (b"\x00" * 16, "16 raw bytes"),
    ],
)
def test_load_key_bytes_rejects_malformed(tmp_path, content, why):
    p = _write_keyfile(tmp_path / "bad", content)
    with pytest.raises(signing.SigningError):
        signing.load_key_bytes(p)


def test_load_key_bytes_missing_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        signing.load_key_bytes(tmp_path / "nope.key")


def _find_on_curve_seed() -> bytes:
    """A deterministic seed that is ITSELF a valid curve point (ambiguous case)."""
    for k in range(1, 256):
        cand = bytes([k]) + bytes(31)
        if ed25519.is_on_curve(cand) and ed25519.publickey(cand) != cand:
            return cand
    raise AssertionError("no on-curve seed found (impossible)")


def _find_off_curve_seed() -> bytes:
    """A deterministic seed that is NOT a valid curve point (unambiguous case)."""
    for k in range(1, 256):
        cand = bytes([k]) + bytes(31)
        if not ed25519.is_on_curve(cand):
            return cand
    raise AssertionError("no off-curve seed found (impossible)")


def test_verification_key_candidates_pubfile(tmp_path):
    # Even a pure public-key file has two interpretations (the bytes as a
    # public key, plus the derived key if the bytes were read as a seed):
    # public key first, deduplicated.
    p = _write_keyfile(tmp_path / "pub.key", PUB)
    assert signing.verification_key_candidates(p) == [PUB, ed25519.publickey(PUB)]


def test_verification_key_candidates_offcurve_seed(tmp_path):
    # A seed that is not itself a curve point: only the derived key is a
    # candidate (the raw bytes are not a valid public key).
    seed = _find_off_curve_seed()
    p = _write_keyfile(tmp_path / "seed.key", seed)
    assert signing.verification_key_candidates(p) == [ed25519.publickey(seed)]


def test_verification_key_candidates_oncurve_seed_ambiguous(tmp_path):
    # An on-curve seed is ambiguous: BOTH interpretations are candidates.
    seed = _find_on_curve_seed()
    p = _write_keyfile(tmp_path / "seed.key", seed)
    assert signing.verification_key_candidates(p) == [seed, ed25519.publickey(seed)]


def test_file_that_signed_can_verify(tmp_path):
    """The CLI promise: verify with the SEED file (not just the .pub)."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    chain.sign_head(log, SEED)
    p = _write_keyfile(tmp_path / "seed.key", SEED)
    report = chain.verify_chain(log, public_key=signing.verification_key_candidates(p))
    assert report.ok is True


# --- signature entries -------------------------------------------------------


def test_entry_fields_and_signature(tmp_path):
    log = tmp_path / "chain.jsonl"
    records = _make_chain(tmp_path, 2)
    entry = signing.new_signature_entry(
        records[-1]["seq"], records[-1]["hash"], SEED, ts="2026-01-01T01:02:03Z")
    assert sorted(entry) == ["head_hash", "public_key", "seq", "sig", "ts"]
    assert entry["seq"] == 2
    assert entry["head_hash"] == records[-1]["hash"]
    assert entry["ts"] == "2026-01-01T01:02:03Z"
    assert entry["public_key"] == PUB.hex()
    assert entry["sig"] == ed25519.sign(
        signing.head_message(2, records[-1]["hash"]), SEED).hex()
    assert signing.entry_is_valid(entry) is True


def test_entry_default_ts_is_canonical_z(tmp_path):
    entry = signing.new_signature_entry(1, "ab" * 32, SEED)
    assert entry["ts"].endswith("Z")
    assert "T" in entry["ts"] and " " not in entry["ts"]


def test_entry_signature_is_deterministic():
    e1 = signing.new_signature_entry(1, "ab" * 32, SEED, ts="2026-01-01T00:00:00Z")
    e2 = signing.new_signature_entry(1, "ab" * 32, SEED, ts="2026-01-01T00:00:00Z")
    assert e1 == e2
    assert e1["sig"] != signing.new_signature_entry(
        1, "ab" * 32, OTHER_SEED, ts="2026-01-01T00:00:00Z")["sig"]


@pytest.mark.parametrize(
    "seq", [0, -1, True, "1"],
)
def test_entry_rejects_bad_seq(seq):
    with pytest.raises(ValueError):
        signing.new_signature_entry(seq, "ab" * 32, SEED)


@pytest.mark.parametrize(
    "head_hash", ["AB" * 32, "ab" * 31, "ab" * 33, "", 123],
)
def test_entry_rejects_bad_head_hash(head_hash):
    with pytest.raises(ValueError):
        signing.new_signature_entry(1, head_hash, SEED)


@pytest.mark.parametrize(
    "seed", [b"", b"\x00" * 31, b"\x00" * 33, b"\x00" * 64],
)
def test_entry_rejects_bad_seed(seed):
    with pytest.raises(ValueError):
        signing.new_signature_entry(1, "ab" * 32, seed)


def _tampered(entry: dict) -> dict:
    return json.loads(json.dumps(entry))


def test_entry_tamper_variants_all_invalid():
    entry = signing.new_signature_entry(1, "ab" * 32, SEED)

    bad = _tampered(entry)
    bad["sig"] = (int(bad["sig"][0], 16) ^ 1).__str__().ljust(1, "0") + bad["sig"][1:]
    assert signing.entry_is_valid(bad) is False

    bad = _tampered(entry)
    bad["head_hash"] = "cd" * 32
    assert signing.entry_is_valid(bad) is False

    bad = _tampered(entry)
    bad["seq"] = 2
    assert signing.entry_is_valid(bad) is False

    bad = _tampered(entry)
    bad["public_key"] = OTHER_PUB.hex()
    assert signing.entry_is_valid(bad) is False

    bad = _tampered(entry)
    bad["sig"] = bad["sig"][:-4]  # truncated
    assert signing.entry_is_valid(bad) is False

    bad = _tampered(entry)
    bad["public_key"] = "zz" * 32  # not hex
    assert signing.entry_is_valid(bad) is False

    bad = _tampered(entry)
    del bad["sig"]
    assert signing.entry_is_valid(bad) is False


# --- sidecar load / write -----------------------------------------------------


def test_load_signatures_missing_and_empty(tmp_path):
    assert signing.load_signatures(tmp_path / "nope.sigs") == []
    empty = tmp_path / "empty.sigs"
    empty.write_text("", encoding="utf-8")
    assert signing.load_signatures(empty) == []


def test_load_signatures_blank_lines_ignored(tmp_path):
    entry = signing.new_signature_entry(1, "ab" * 32, SEED)
    p = tmp_path / "sigs"
    p.write_text("\n" + json.dumps(entry, sort_keys=True) + "\n\n", encoding="utf-8")
    assert signing.load_signatures(p) == [entry]


def test_load_signatures_sigs_path_is_sibling(tmp_path):
    assert signing.sigs_path(tmp_path / "logs" / "chain.jsonl") == \
        tmp_path / "logs" / "chain.jsonl.sigs"
    assert chain.SIGS_SUFFIX == ".sigs"
    assert chain.sigs_path(tmp_path / "c.jsonl") == tmp_path / "c.jsonl.sigs"


@pytest.mark.parametrize(
    "line,match",
    [
        ("not json", "not valid JSON"),
        ("[1,2]", "must be a JSON object"),
        (json.dumps({"seq": 1, "head_hash": "ab" * 32}), "'public_key' must be"),
        (json.dumps({"seq": "x", "head_hash": "ab" * 32, "public_key": "ab" * 32,
                     "sig": "ab", "ts": "t"}), "'seq' must be"),
        (json.dumps({"seq": 1, "head_hash": "ab" * 32, "public_key": "ab" * 32,
                     "sig": "ab"}), "'ts' must be"),
    ],
)
def test_load_signatures_rejects_bad_lines(tmp_path, line, match):
    good = json.dumps(signing.new_signature_entry(1, "ab" * 32, SEED))
    p = tmp_path / "sigs"
    p.write_text(good + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(signing.SigningError, match=match):
        signing.load_signatures(p)


# --- chain.sign_head -----------------------------------------------------------


def test_sign_head_appends_sidecar_entry(tmp_path):
    log = tmp_path / "chain.jsonl"
    records = _make_chain(tmp_path, 3)
    entry = chain.sign_head(log, SEED, ts="2026-01-01T09:00:00Z")
    assert entry["seq"] == 3
    assert entry["head_hash"] == records[-1]["hash"]
    sigs = tmp_path / "chain.jsonl.sigs"
    lines = sigs.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == entry
    # Compact, sorted-key JSONL like the chain itself.
    assert lines[0] == json.dumps(entry, sort_keys=True, separators=(",", ":"),
                                  ensure_ascii=True)
    # The log itself is untouched.
    assert (tmp_path / "chain.jsonl").read_text(encoding="utf-8") == \
        "\n".join(to_json_line(r) for r in records) + "\n"


def test_sign_head_refuses_empty_chain(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_text("", encoding="utf-8")
    with pytest.raises(chain.LogError, match="empty chain"):
        chain.sign_head(log, SEED)
    with pytest.raises(chain.LogError, match="empty chain"):
        chain.sign_head(tmp_path / "missing.jsonl", SEED)


def test_sign_head_refuses_corrupt_chain(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    lines = log.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["data"] = "tampered"
    log.write_text(to_json_line(rec) + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(chain.LogError, match="corrupt chain"):
        chain.sign_head(log, SEED)
    assert not (tmp_path / "chain.jsonl.sigs").exists()


def test_sign_head_refuses_duplicate(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    with pytest.raises(signing.SigningError, match="already signed"):
        chain.sign_head(log, SEED)
    # A DIFFERENT key may sign the same head.
    entry2 = chain.sign_head(log, OTHER_SEED)
    assert entry2["public_key"] == OTHER_PUB.hex()
    lines = (tmp_path / "chain.jsonl.sigs").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_sign_head_follows_new_head(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    chain.sign_head(log, SEED)
    third = chain.append_event(log, "build", {"i": 99},
                               ts="2026-01-01T00:00:03Z")
    entry = chain.sign_head(log, SEED)
    assert entry["seq"] == 3
    assert entry["head_hash"] == third["hash"]
    lines = (tmp_path / "chain.jsonl.sigs").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(l)["seq"] for l in lines] == [2, 3]


def test_sign_head_rejects_bad_seed(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    with pytest.raises(ValueError, match="32 bytes"):
        chain.sign_head(log, b"\x00" * 31)


# --- verify_chain(public_key=...) ----------------------------------------------


def test_verify_with_public_key_signed_head_ok(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    chain.sign_head(log, SEED)
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is True
    assert report.record_count == 2
    assert report.signature_count == 1


def test_verify_with_public_key_unsigned_head_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is False
    assert report.reason is not None
    assert "not anchored" in report.reason
    assert report.signature_count == 0


def test_verify_with_public_key_wrong_key_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    chain.sign_head(log, SEED)
    report = chain.verify_chain(log, public_key=OTHER_PUB)
    assert report.ok is False
    assert "not anchored" in report.reason


def test_verify_with_public_key_candidate_list(tmp_path):
    """A list of candidate public keys (from a seed-or-pub key file) works."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    report = chain.verify_chain(log, public_key=[OTHER_PUB, PUB])
    assert report.ok is True


def test_verify_public_key_stale_head_after_append_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    chain.sign_head(log, SEED)
    chain.append_event(log, "build", {"i": 99}, ts="2026-01-01T00:00:03Z")
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is False
    assert "no covering signature" in report.reason
    assert "seq 3" in report.reason


def test_verify_public_key_detects_truncation_of_signed_chain(tmp_path):
    """A signature for head seq 3 makes a truncation to seq 2 provable even
    WITHOUT --anchor: the current head (seq 2) is not covered."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3)
    chain.sign_head(log, SEED)
    lines = log.read_text(encoding="utf-8").splitlines()
    log.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is False
    assert "not anchored" in report.reason
    assert "seq 2" in report.reason
    # Without the key, the stale (but valid) signature for seq 3 still
    # self-verifies and the prefix chain is fine: OK.
    assert chain.verify_chain(log).ok is True


def test_verify_public_key_tampered_chain_reports_tamper_first(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    chain.sign_head(log, SEED)
    lines = log.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["data"] = "evil"
    log.write_text(lines[0] + "\n" + to_json_line(rec) + "\n", encoding="utf-8")
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is False
    assert report.first_bad_seq == 2
    assert "hash mismatch" in report.reason  # chain finding, not the sig one


def test_verify_rejects_tampered_signature_log_without_key(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    sigs = tmp_path / "chain.jsonl.sigs"
    entry = json.loads(sigs.read_text(encoding="utf-8").splitlines()[0])
    entry["sig"] = str(int(entry["sig"][-1], 16) ^ 1) + entry["sig"][:-1]
    sigs.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    report = chain.verify_chain(log)  # NO public_key: sidecar still re-checked
    assert report.ok is False
    assert "signature log" in report.reason
    assert "line 1" in report.reason


def test_verify_rejects_signature_log_entry_for_wrong_hash(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    sigs = tmp_path / "chain.jsonl.sigs"
    entry = json.loads(sigs.read_text(encoding="utf-8").splitlines()[0])
    entry["head_hash"] = "cd" * 32  # sig no longer matches its own fields
    sigs.write_text(json.dumps(entry, sort_keys=True) + "\n", encoding="utf-8")
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is False
    assert "signature log" in report.reason


def test_verify_rejects_garbage_signature_log_line(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    sigs = tmp_path / "chain.jsonl.sigs"
    sigs.write_text(sigs.read_text(encoding="utf-8") + "garbage\n", encoding="utf-8")
    report = chain.verify_chain(log)
    assert report.ok is False
    assert "signature log" in report.reason
    assert "line 2" in report.reason


def test_verify_empty_chain_with_public_key_fails(tmp_path):
    log = tmp_path / "empty.jsonl"
    log.write_text("", encoding="utf-8")
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is False
    assert "empty chain" in report.reason
    # ...but without a key the empty chain is still a valid empty chain.
    assert chain.verify_chain(log).ok is True


def test_signature_count_is_none_when_not_consulted(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    assert chain.verify_chain(log).signature_count is None


def test_verify_ignores_foreign_key_signature_for_coverage(tmp_path):
    """An entry signed by a DIFFERENT key does not cover --public-key, but
    is still a valid stored signature (no log-integrity failure)."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, OTHER_SEED)
    report = chain.verify_chain(log, public_key=PUB)
    assert report.ok is False
    assert "not anchored" in report.reason
    # No key: the foreign-key signature is valid and stored.
    assert chain.verify_chain(log).ok is True


# --- CLI: keygen ---------------------------------------------------------------


def test_cli_keygen_exit_zero(tmp_path, capsys):
    seed_path = tmp_path / "attest.key"
    rc = main(["keygen", "--seed", str(seed_path)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    seed = seed_path.read_bytes()
    pub = (tmp_path / "attest.key.pub").read_bytes()
    assert ed25519.publickey(seed) == pub
    assert _mode(seed_path) == 0o600
    assert _mode(tmp_path / "attest.key.pub") == 0o644
    assert "generated ed25519 keypair" in out
    assert f"public key: {pub.hex()}" in out


def test_cli_keygen_refuses_overwrite(tmp_path, capsys):
    seed_path = tmp_path / "k.key"
    assert main(["keygen", "--seed", str(seed_path)]) == EXIT_OK
    capsys.readouterr()
    rc = main(["keygen", "--seed", str(seed_path)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "refusing to overwrite" in err
    # The seed file is untouched.
    assert seed_path.read_bytes()


def test_cli_keygen_seed_required():
    with pytest.raises(SystemExit) as exc:
        main(["keygen"])
    assert exc.value.code == EXIT_USAGE


# --- CLI: sign -------------------------------------------------------------------


def test_cli_sign_exit_zero(tmp_path, capsys):
    seed_path = tmp_path / "k.key"
    assert main(["keygen", "--seed", str(seed_path)]) == EXIT_OK
    capsys.readouterr()
    log = tmp_path / "chain.jsonl"
    head = chain.append_event(log, "build", {"i": 0}, ts="2026-01-01T00:00:00Z")
    rc = main(["sign", "--log", str(log), "--signing-key", str(seed_path)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert f"signed seq=1 head={head['hash']}" in out
    entry = json.loads(
        (tmp_path / "chain.jsonl.sigs").read_text(encoding="utf-8").splitlines()[0])
    assert entry["seq"] == 1
    assert entry["head_hash"] == head["hash"]
    assert entry["public_key"] == (tmp_path / "k.key.pub").read_bytes().hex()


def test_cli_sign_hex_text_keyfile(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    chain.append_event(log, "build", None, ts="2026-01-01T00:00:00Z")
    keyfile = tmp_path / "key.txt"
    keyfile.write_text(SEED.hex() + "\n", encoding="ascii")
    rc = main(["sign", "--log", str(log), "--signing-key", str(keyfile)])
    assert rc == EXIT_OK
    capsys.readouterr()


def test_cli_sign_corrupt_chain_exit_one(tmp_path, capsys):
    seed_path = tmp_path / "k.key"
    main(["keygen", "--seed", str(seed_path)])
    capsys.readouterr()
    log = tmp_path / "chain.jsonl"
    chain.append_event(log, "build", None, ts="2026-01-01T00:00:00Z")
    log.write_text("garbage\n", encoding="utf-8")
    rc = main(["sign", "--log", str(log), "--signing-key", str(seed_path)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "corrupt chain" in err


def test_cli_sign_empty_chain_exit_one(tmp_path, capsys):
    seed_path = tmp_path / "k.key"
    main(["keygen", "--seed", str(seed_path)])
    capsys.readouterr()
    rc = main(["sign", "--log", str(tmp_path / "empty.jsonl"),
               "--signing-key", str(seed_path)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "empty chain" in err


def test_cli_sign_duplicate_exit_one(tmp_path, capsys):
    seed_path = tmp_path / "k.key"
    main(["keygen", "--seed", str(seed_path)])
    capsys.readouterr()
    log = tmp_path / "chain.jsonl"
    chain.append_event(log, "build", None, ts="2026-01-01T00:00:00Z")
    assert main(["sign", "--log", str(log), "--signing-key", str(seed_path)]) == EXIT_OK
    capsys.readouterr()
    rc = main(["sign", "--log", str(log), "--signing-key", str(seed_path)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "already signed" in err


def test_cli_sign_bad_keyfile_exit_two(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    chain.append_event(log, "build", None, ts="2026-01-01T00:00:00Z")
    bad = tmp_path / "bad.key"
    bad.write_bytes(b"\x00" * 31)
    rc = main(["sign", "--log", str(log), "--signing-key", str(bad)])
    err = capsys.readouterr().err
    assert rc == EXIT_USAGE
    assert "not 32 raw bytes" in err


def test_cli_sign_missing_keyfile_exit_two(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    chain.append_event(log, "build", None, ts="2026-01-01T00:00:00Z")
    rc = main(["sign", "--log", str(log), "--signing-key",
               str(tmp_path / "nope.key")])
    assert rc == EXIT_USAGE
    assert "cannot read key file" in capsys.readouterr().err


def test_cli_sign_requires_key():
    with pytest.raises(SystemExit) as exc:
        main(["sign", "--log", "c.jsonl"])
    assert exc.value.code == EXIT_USAGE


# --- CLI: verify --public-key ------------------------------------------------------


def test_cli_verify_public_key_ok(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2)
    chain.sign_head(log, SEED)
    pubfile = _write_keyfile(tmp_path / "pub.key", PUB)
    rc = main(["verify", "--log", str(log), "--public-key", str(pubfile)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "OK: chain verified (2 records)" in out
    assert f"head signed by key {PUB.hex()[:16]}..." in out


def test_cli_verify_public_key_with_seed_file(tmp_path, capsys):
    """The file that signed (a seed) can verify."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    seedfile = _write_keyfile(tmp_path / "seed.key", SEED)
    rc = main(["verify", "--log", str(log), "--public-key", str(seedfile)])
    assert rc == EXIT_OK
    capsys.readouterr()


def test_cli_verify_public_key_unsigned_exit_one(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    pubfile = _write_keyfile(tmp_path / "pub.key", PUB)
    rc = main(["verify", "--log", str(log), "--public-key", str(pubfile)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "FAIL:" in err
    assert "not anchored" in err


def test_cli_verify_public_key_wrong_key_exit_one(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    pubfile = _write_keyfile(tmp_path / "other.pub", OTHER_PUB)
    rc = main(["verify", "--log", str(log), "--public-key", str(pubfile)])
    assert rc == EXIT_FAIL
    assert "not anchored" in capsys.readouterr().err


def test_cli_verify_public_key_bad_file_exit_two(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    bad = tmp_path / "bad.key"
    bad.write_bytes(b"nope")
    rc = main(["verify", "--log", str(log), "--public-key", str(bad)])
    assert rc == EXIT_USAGE
    assert "not 32 raw bytes" in capsys.readouterr().err


def test_cli_verify_public_key_missing_file_exit_two(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    rc = main(["verify", "--log", str(log), "--public-key",
               str(tmp_path / "nope.key")])
    assert rc == EXIT_USAGE
    assert "cannot read key file" in capsys.readouterr().err


def test_cli_verify_stored_signatures_noted(tmp_path, capsys):
    """Without --public-key, a sidecar with valid entries is re-checked and
    noted in the OK line (2 entries here: two keys signing the same head)."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1)
    chain.sign_head(log, SEED)
    chain.sign_head(log, OTHER_SEED)
    rc = main(["verify", "--log", str(log)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "2 stored signatures verified" in out


def test_end_to_end_keygen_sign_verify(tmp_path, capsys):
    """The full operator flow: keygen → append → sign → append → verify
    fails (stale) → sign again → verify passes."""
    seed_path = tmp_path / "ops.key"
    assert main(["keygen", "--seed", str(seed_path)]) == EXIT_OK
    capsys.readouterr()
    log = tmp_path / "chain.jsonl"
    assert main(["append", "--log", str(log), "--type", "build",
                 "--data", '{"commit": "abc"}']) == EXIT_OK
    assert main(["sign", "--log", str(log), "--signing-key",
                 str(seed_path)]) == EXIT_OK
    capsys.readouterr()
    pubfile = tmp_path / "ops.key.pub"
    assert main(["verify", "--log", str(log), "--public-key",
                 str(pubfile)]) == EXIT_OK
    capsys.readouterr()
    # A new head invalidates the old signature.
    assert main(["append", "--log", str(log), "--type", "deploy",
                 "--data", '{"host": "h1"}']) == EXIT_OK
    capsys.readouterr()
    rc = main(["verify", "--log", str(log), "--public-key", str(pubfile)])
    assert rc == EXIT_FAIL
    err = capsys.readouterr().err
    assert "not anchored" in err
    assert main(["sign", "--log", str(log), "--signing-key",
                 str(seed_path)]) == EXIT_OK
    capsys.readouterr()
    assert main(["verify", "--log", str(log), "--public-key",
                 str(pubfile)]) == EXIT_OK
    # Two entries now: one per head.
    sigs = (tmp_path / "chain.jsonl.sigs").read_text(encoding="utf-8").splitlines()
    assert [json.loads(l)["seq"] for l in sigs] == [1, 2]
