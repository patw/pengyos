"""Tests for the export subtask.

Covers:

* ``chain.merkle_root`` — the Merkle rule (single / even / odd leaf counts,
  the duplicate-odd rule, order sensitivity, the empty-chain root),
* ``chain.export_chain`` — bundle shape and values, empty chains, corrupt
  source refusal, atomic writes (no stray temp files),
* ``chain.verify_export`` — clean bundles pass; every tamper class (edited
  record, dropped tail, stale root / head / count, wrong format) is caught,
* the ``export`` CLI end-to-end (exit codes, output lines, corrupt source).
"""

import hashlib
import json
from pathlib import Path

import pytest

from pengyos_attest import chain, signing
from pengyos_attest.chain import (
    EMPTY_CHAIN_ROOT,
    EXPORT_FORMAT,
    EXPORT_FORMAT_VERSION,
    SUPPORTED_EXPORT_FORMAT_VERSIONS,
    chain_length,
    export_chain,
    merkle_root,
    verify_export,
)
from pengyos_attest.cli import EXIT_FAIL, EXIT_OK, main

# Fixed 64-hex leaves for the pure Merkle-structure tests.
H1 = "11" * 32
H2 = "22" * 32
H3 = "33" * 32
H4 = "44" * 32
H5 = "55" * 32


def _pab(left: str, right: str) -> str:
    """One Merkle parent: sha256 over the ASCII concatenation."""
    return hashlib.sha256((left + right).encode("ascii")).hexdigest()


def _build_chain(tmp_path: Path, n: int = 3) -> Path:
    log = tmp_path / "chain.jsonl"
    for i in range(1, n + 1):
        chain.append_event(log, f"t{i}", {"i": i}, ts=f"2026-01-01T00:00:0{i}Z")
    return log


def _load(bundle_path: Path) -> dict:
    return json.loads(bundle_path.read_text(encoding="utf-8"))


# --- merkle_root ------------------------------------------------------------


def test_merkle_root_empty_is_sha256_of_empty_bytes():
    assert EMPTY_CHAIN_ROOT == hashlib.sha256(b"").hexdigest()
    assert merkle_root([]) == EMPTY_CHAIN_ROOT


def test_merkle_root_single_leaf_is_the_leaf():
    assert merkle_root([H1]) == H1


def test_merkle_root_two_leaves_is_one_parent():
    assert merkle_root([H1, H2]) == _pab(H1, H2)


def test_merkle_root_four_leaves_is_two_levels():
    root = merkle_root([H1, H2, H3, H4])
    assert root == _pab(_pab(H1, H2), _pab(H3, H4))


def test_merkle_root_three_leaves_duplicates_odd_final_node():
    root = merkle_root([H1, H2, H3])
    # Level 1: [H1, H2, H3, H3] -> parents (H1,H2) and (H3,H3); level 2 pairs.
    assert root == _pab(_pab(H1, H2), _pab(H3, H3))


def test_merkle_root_five_leaves_two_rounds_of_pairing():
    root = merkle_root([H1, H2, H3, H4, H5])
    # Level 1: [H1..H5, H5] -> 4 parents; level 2 pairs the two pairs.
    level1 = [_pab(H1, H2), _pab(H3, H4), _pab(H5, H5)]
    assert root == _pab(_pab(level1[0], level1[1]), _pab(level1[2], level1[2]))


def test_merkle_root_is_order_sensitive():
    assert merkle_root([H1, H2]) != merkle_root([H2, H1])


def test_merkle_root_changes_when_any_leaf_changes():
    base = [H1, H2, H3, H4]
    for i in range(len(base)):
        mutated = list(base)
        mutated[i] = "ee" * 32
        assert merkle_root(mutated) != merkle_root(base)


# --- export_chain -----------------------------------------------------------


def test_export_chain_bundle_shape_and_values(tmp_path):
    log = _build_chain(tmp_path, 3)
    recs = chain.read_records(log)
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out, ts="2026-01-02T00:00:00Z")

    assert bundle["format"] == EXPORT_FORMAT
    assert bundle["format_version"] == EXPORT_FORMAT_VERSION
    assert bundle["exported_at"] == "2026-01-02T00:00:00Z"
    assert bundle["record_count"] == 3
    assert bundle["head_hash"] == recs[-1]["hash"]
    assert bundle["root_hash"] == merkle_root([r["hash"] for r in recs])
    assert bundle["records"] == recs
    # The file on disk holds exactly the bundle (pretty, sorted, newline).
    text = out.read_text(encoding="utf-8")
    assert json.loads(text) == bundle
    assert text == json.dumps(bundle, sort_keys=True, indent=2) + "\n"


def test_export_defaults_exported_at_to_valid_timestamp(tmp_path):
    log = _build_chain(tmp_path, 1)
    bundle = export_chain(log, tmp_path / "b.json")
    ts = bundle["exported_at"]
    assert ts.endswith("Z") and len(ts) >= 20
    from pengyos_attest.record import _TS_RE  # noqa: PLC0415 - format check only

    assert _TS_RE.match(ts)


def test_export_missing_log_is_valid_empty_bundle(tmp_path):
    out = tmp_path / "bundle.json"
    bundle = export_chain(tmp_path / "nope.jsonl", out)
    assert bundle["record_count"] == 0
    assert bundle["head_hash"] is None
    assert bundle["root_hash"] == EMPTY_CHAIN_ROOT
    assert bundle["records"] == []
    assert bundle["sigs"] == []  # v2: no sidecar -> empty sig snapshot
    assert verify_export(_load(out)) is None


def test_export_corrupt_source_raises_logerror(tmp_path):
    log = _build_chain(tmp_path, 2)
    lines = log.read_text(encoding="utf-8").splitlines()
    # Edit record 1's data: its hash no longer recomputes.
    bad = json.loads(lines[0])
    bad["data"] = {"i": 999}
    log.write_text(json.dumps(bad, sort_keys=True) + "\n" + lines[1] + "\n",
                   encoding="utf-8")
    with pytest.raises(chain.LogError, match="corrupt chain"):
        export_chain(log, tmp_path / "b.json")


def test_export_leaves_no_stray_temp_files(tmp_path):
    log = _build_chain(tmp_path, 3)
    out = tmp_path / "sub" / "bundle.json"  # nested dir gets created too
    export_chain(log, out)
    export_chain(log, out)  # re-export overwrites cleanly
    # The log dir holds the chain plus its expected .lock sidecar (item 6);
    # the bundle dir holds only the bundle (no temp-file debris).
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "chain.jsonl", "chain.jsonl.lock", "sub",
    ]
    assert sorted(p.name for p in out.parent.iterdir()) == ["bundle.json"]
    assert verify_export(_load(out)) is None


# --- verify_export ----------------------------------------------------------


def test_verify_export_clean_roundtrip(tmp_path):
    log = _build_chain(tmp_path, 3)
    out = tmp_path / "bundle.json"
    export_chain(log, out)
    assert verify_export(_load(out)) is None
    # It works offline: deleting the source log changes nothing.
    log.unlink()
    assert verify_export(_load(out)) is None


def test_verify_export_edited_record_is_caught(tmp_path):
    out = _build_bundle(tmp_path, 3)
    bundle = _load(out)
    bundle["records"][1]["data"] = {"tampered": True}
    err = verify_export(bundle)
    assert err is not None
    assert "do not form a valid chain" in err


def test_verify_export_dropped_tail_is_caught(tmp_path):
    out = _build_bundle(tmp_path, 3)
    bundle = _load(out)
    bundle["records"] = bundle["records"][:2]  # drop record 3, keep the root
    assert verify_export(bundle) is not None


def _build_bundle(tmp_path: Path, n: int = 3) -> Path:
    log = _build_chain(tmp_path, n)
    out = tmp_path / "bundle.json"
    export_chain(log, out)
    return out


def test_verify_export_stale_root_is_caught(tmp_path):
    out = _build_bundle(tmp_path, 3)
    bundle = _load(out)
    bundle["root_hash"] = "ab" * 32
    assert "root_hash" in verify_export(bundle)


def test_verify_export_stale_head_is_caught(tmp_path):
    out = _build_bundle(tmp_path, 3)
    bundle = _load(out)
    bundle["head_hash"] = "cd" * 32
    assert "head_hash" in verify_export(bundle)


def test_verify_export_wrong_record_count_is_caught(tmp_path):
    out = _build_bundle(tmp_path, 3)
    bundle = _load(out)
    bundle["record_count"] = 2
    assert "record_count" in verify_export(bundle)


def test_verify_export_wrong_format_is_caught(tmp_path):
    out = _build_bundle(tmp_path, 2)
    bundle = _load(out)
    bundle["format"] = "someone-else/export"
    err = verify_export(bundle)
    assert err is not None and "format" in err


def test_verify_export_rejects_non_object_and_non_list(tmp_path):
    assert "JSON object" in verify_export(["not", "a", "dict"])
    out = _build_bundle(tmp_path, 1)
    bundle = _load(out)
    bundle["records"] = "nope"
    assert "must be a list" in verify_export(bundle)


def test_verify_export_rejects_non_object_record(tmp_path):
    out = _build_bundle(tmp_path, 2)
    bundle = _load(out)
    bundle["records"][0] = "string-record"
    err = verify_export(bundle)
    assert err is not None and "records[0]" in err


def test_verify_export_reordered_records_are_caught(tmp_path):
    out = _build_bundle(tmp_path, 3)
    bundle = _load(out)
    bundle["records"][0], bundle["records"][2] = (
        bundle["records"][2], bundle["records"][0])
    err = verify_export(bundle)
    assert err is not None and "do not form a valid chain" in err


# --- CLI export --------------------------------------------------------------


def test_cli_export_exit_ok_and_bundle_valid(tmp_path, capsys):
    log = _build_chain(tmp_path, 3)
    out = tmp_path / "bundle.json"
    rc = main(["export", "--log", str(log), "--out", str(out)])
    out_text = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "exported 3 records to" in out_text
    bundle = _load(out)
    assert bundle["root_hash"] in out_text
    assert verify_export(bundle) is None


def test_cli_export_singular_grammar(tmp_path, capsys):
    log = _build_chain(tmp_path, 1)
    rc = main(["export", "--log", str(log), "--out", str(tmp_path / "b.json")])
    assert rc == EXIT_OK
    assert "exported 1 record to" in capsys.readouterr().out


def test_cli_export_missing_log_exits_ok_empty_bundle(tmp_path, capsys):
    out = tmp_path / "bundle.json"
    rc = main(["export", "--log", str(tmp_path / "nope.jsonl"), "--out", str(out)])
    assert rc == EXIT_OK
    assert "exported 0 records to" in capsys.readouterr().out
    bundle = _load(out)
    assert bundle["record_count"] == 0
    assert verify_export(bundle) is None


def test_cli_export_corrupt_source_is_operation_failure(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    log.write_text("garbage\n", encoding="utf-8")
    rc = main(["export", "--log", str(log), "--out", str(tmp_path / "b.json")])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "corrupt chain" in err
    assert not (tmp_path / "b.json").exists()


def test_cli_export_unwritable_out_is_operation_failure(tmp_path, capsys):
    log = _build_chain(tmp_path, 1)
    # A path whose parent is an existing FILE: the mkdir fails -> OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    rc = main(["export", "--log", str(log),
               "--out", str(blocker / "b.json")])
    assert rc == EXIT_FAIL
    assert "cannot write export" in capsys.readouterr().err


def test_exported_chain_still_verifies_after_export(tmp_path):
    # Exporting must not mutate the log.
    log = _build_chain(tmp_path, 3)
    before = log.read_text(encoding="utf-8")
    export_chain(log, tmp_path / "b.json")
    assert log.read_text(encoding="utf-8") == before
    assert chain_length(log) == 3
    assert chain.verify_chain(log).ok


# --- export bundle format v2: the signature sidecar in the bundle ----------

def _seed_for_tests() -> bytes:
    # A FIXED seed so signed bundles are deterministic and diffable.
    # (Curve membership does not matter here — signature entries carry
    # their own public_key, so no seed/public-key ambiguity arises; the
    # second signer uses a different fixed seed 0x99…, which is a
    # DIFFERENT key and therefore allowed to sign the same head.)
    return bytes([0x77]) + bytes(31)


def _sign_head_of(log: Path, seed: bytes, ts: str = "2026-01-03T00:00:00Z"):
    return chain.sign_head(log, seed, ts=ts)


def test_supported_versions_are_v1_and_v2():
    assert EXPORT_FORMAT_VERSION == 2
    assert SUPPORTED_EXPORT_FORMAT_VERSIONS == (1, 2)


def test_export_v2_shape_includes_sigs_snapshot(tmp_path):
    log = _build_chain(tmp_path, 2)
    seed = _seed_for_tests()
    entry = _sign_head_of(log, seed)
    sigs_file = signing.sigs_path(log)
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out, ts="2026-01-02T00:00:00Z")

    assert bundle["format_version"] == 2
    # The bundle carries the EXACT sidecar content (byte order preserved).
    assert bundle["sigs"] == [entry]
    sidecar_lines = sigs_file.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in sidecar_lines] == bundle["sigs"]
    assert all(signing.entry_is_valid(e) for e in bundle["sigs"])
    assert verify_export(_load(out)) is None
    # The bundle is still the canonical pretty-JSON document (new key in).
    text = out.read_text(encoding="utf-8")
    assert text == json.dumps(bundle, sort_keys=True, indent=2) + "\n"


def test_export_v2_unsigned_chain_has_empty_sigs(tmp_path):
    log = _build_chain(tmp_path, 2)  # never signed, no sidecar file
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out)
    assert not signing.sigs_path(log).exists()
    assert bundle["sigs"] == []
    assert verify_export(_load(out)) is None


def test_export_v2_stale_signature_is_still_captured(tmp_path):
    # Signing head seq=1, then appending: the sidecar holds a VALID entry
    # that no longer covers the head. The bundle must still carry it
    # (it is valid stored anchoring evidence) and verify_export must pass —
    # head coverage is a verify --public-key concern, not a bundle concern.
    log = _build_chain(tmp_path, 1)
    seed = _seed_for_tests()
    old_entry = _sign_head_of(log, seed, ts="2026-01-03T00:00:00Z")
    chain.append_event(log, "t2", {"i": 2}, ts="2026-01-01T00:00:02Z")
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out)
    assert bundle["record_count"] == 2
    assert bundle["sigs"] == [old_entry]
    assert bundle["sigs"][0]["seq"] == 1
    assert bundle["sigs"][0]["head_hash"] != bundle["head_hash"]
    assert verify_export(_load(out)) is None


def test_export_v2_multiple_signatures_preserve_sidecar_order(tmp_path):
    log = _build_chain(tmp_path, 1)
    seed_a = _seed_for_tests()
    seed_b = bytes([0x99]) + bytes(31)
    entry_a = _sign_head_of(log, seed_a, ts="2026-01-03T00:00:00Z")
    entry_b = _sign_head_of(log, seed_b, ts="2026-01-03T00:00:01Z")
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out)
    assert bundle["sigs"] == [entry_a, entry_b]  # file order preserved
    assert verify_export(_load(out)) is None


def test_export_v2_corrupt_sidecar_is_refused(tmp_path):
    # A sidecar with a malformed line fails verify_chain's signature
    # re-check, so export must refuse to bundle it.
    log = _build_chain(tmp_path, 2)
    _sign_head_of(log, _seed_for_tests())
    sigs_file = signing.sigs_path(log)
    sigs_file.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(chain.LogError, match="corrupt chain"):
        export_chain(log, tmp_path / "b.json")
    assert not (tmp_path / "b.json").exists()


def test_export_v2_invalid_signature_in_sidecar_is_refused(tmp_path):
    # Well-formed shape but a signature that does not verify: the entry
    # is caught by verify_chain's self-consistency re-check pre-export.
    log = _build_chain(tmp_path, 2)
    entry = _sign_head_of(log, _seed_for_tests())
    bad = dict(entry)
    bad["sig"] = "00" * 64  # wrong signature bytes
    sigs_file = signing.sigs_path(log)
    sigs_file.write_text(json.dumps(bad, sort_keys=True, separators=(",", ":"))
                         + "\n", encoding="utf-8")
    with pytest.raises(chain.LogError, match="corrupt chain"):
        export_chain(log, tmp_path / "b.json")


# --- verify_export: v2 signature checks -------------------------------------

def _build_signed_bundle(tmp_path: Path, n: int = 2) -> Path:
    """Export a chain whose head is signed once (bundle with one sig entry)."""
    log = _build_chain(tmp_path, n)
    _sign_head_of(log, _seed_for_tests())
    out = tmp_path / "bundle.json"
    export_chain(log, out)
    return out


def test_verify_export_v2_tampered_sig_bytes_is_caught(tmp_path):
    out = _build_signed_bundle(tmp_path, 2)
    bundle = _load(out)
    entry = bundle["sigs"][0]
    # Flip one hex digit of the signature.
    sig = entry["sig"]
    entry["sig"] = ("0" if sig[0] != "0" else "1") + sig[1:]
    err = verify_export(bundle)
    assert err is not None
    assert "sigs[0]" in err and "signature invalid" in err


def test_verify_export_v2_edited_seq_in_sig_is_caught(tmp_path):
    # An entry with a DIFFERENT seq than the one its signature covers is
    # exactly what 'sigs are self-verifying' must reject.
    out = _build_signed_bundle(tmp_path, 2)
    bundle = _load(out)
    bundle["sigs"][0]["seq"] = 99
    err = verify_export(bundle)
    assert err is not None and "sigs[0]" in err and "signature invalid" in err


def test_verify_export_v2_malformed_shape_is_caught(tmp_path):
    out = _build_signed_bundle(tmp_path, 2)
    bundle = _load(out)
    bundle["sigs"][0]["seq"] = "1"  # string, not an integer
    err = verify_export(bundle)
    assert err is not None and "sigs[0]" in err and "'seq'" in err


def test_verify_export_v2_non_object_sig_entry_is_caught(tmp_path):
    out = _build_signed_bundle(tmp_path, 2)
    bundle = _load(out)
    bundle["sigs"][0] = "a-string"
    err = verify_export(bundle)
    assert err is not None and "sigs[0]" in err


def test_verify_export_v2_missing_sigs_key_is_caught(tmp_path):
    out = _build_signed_bundle(tmp_path, 2)
    bundle = _load(out)
    del bundle["sigs"]
    err = verify_export(bundle)
    assert err is not None and "sigs" in err and "list" in err


def test_verify_export_v2_sigs_not_a_list_is_caught(tmp_path):
    out = _build_signed_bundle(tmp_path, 2)
    bundle = _load(out)
    bundle["sigs"] = "nope"
    err = verify_export(bundle)
    assert err is not None and "sigs" in err and "list" in err


# --- verify_export: v1 compatibility ----------------------------------------

def test_verify_export_v1_bundle_without_sigs_still_verifies(tmp_path):
    # A pre-signing v1 bundle must remain verifiable forever.
    log = _build_chain(tmp_path, 3)
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out)
    v1 = {k: v for k, v in bundle.items() if k != "sigs"}
    v1["format_version"] = 1
    assert verify_export(v1) is None


def test_verify_export_v1_bundle_with_sigs_is_rejected(tmp_path):
    # v1 never carried signatures; tolerating a 'sigs' key on a v1 bundle
    # would let unverifiable-by-the-v2-contract entries be smuggled in.
    log = _build_chain(tmp_path, 2)
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out)
    v1 = dict(bundle)
    v1["format_version"] = 1
    err = verify_export(v1)
    assert err is not None and "must not carry" in err and "sigs" in err


def test_verify_export_unknown_format_version_is_rejected(tmp_path):
    log = _build_chain(tmp_path, 2)
    out = tmp_path / "bundle.json"
    bundle = export_chain(log, out)
    for bogus in (0, 3, "2", None):
        mutated = dict(bundle)
        mutated["format_version"] = bogus
        err = verify_export(mutated)
        assert err is not None and "format_version" in err, bogus


def test_verify_export_tampered_record_in_v2_bundle_still_caught(tmp_path):
    # The signature checks do not weaken the record checks.
    out = _build_bundle(tmp_path, 2)
    bundle = _load(out)
    bundle["records"][0]["type"] = "evil"
    err = verify_export(bundle)
    assert err is not None and "do not form a valid chain" in err


# --- CLI export, v2 ---------------------------------------------------------

def test_cli_export_signed_chain_reports_signature_count(tmp_path, capsys):
    log = _build_chain(tmp_path, 2)
    _sign_head_of(log, _seed_for_tests())
    out = tmp_path / "bundle.json"
    rc = main(["export", "--log", str(log), "--out", str(out)])
    printed = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "exported 2 records to" in printed
    assert "1 signature" in printed
    bundle = _load(out)
    assert len(bundle["sigs"]) == 1
    assert verify_export(bundle) is None


def test_cli_export_plural_signatures(tmp_path, capsys):
    log = _build_chain(tmp_path, 1)
    _sign_head_of(log, _seed_for_tests(), ts="2026-01-03T00:00:00Z")
    _sign_head_of(log, bytes([0x99]) + bytes(31), ts="2026-01-03T00:00:01Z")
    rc = main(["export", "--log", str(log), "--out", str(tmp_path / "b.json")])
    assert rc == EXIT_OK
    assert "2 signatures" in capsys.readouterr().out


def test_cli_export_unsigned_chain_has_no_signature_note(tmp_path, capsys):
    log = _build_chain(tmp_path, 2)
    rc = main(["export", "--log", str(log), "--out", str(tmp_path / "b.json")])
    assert rc == EXIT_OK
    printed = capsys.readouterr().out
    assert "exported 2 records to" in printed
    assert "signature" not in printed


def test_cli_export_corrupt_sidecar_is_operation_failure(tmp_path, capsys):
    log = _build_chain(tmp_path, 2)
    signing.sigs_path(log).write_text("garbage\n", encoding="utf-8")
    rc = main(["export", "--log", str(log), "--out", str(tmp_path / "b.json")])
    assert rc == EXIT_FAIL
    assert "corrupt chain" in capsys.readouterr().err
    assert not (tmp_path / "b.json").exists()
