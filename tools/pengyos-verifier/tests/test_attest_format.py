"""Tests for the attest record format (attest.py).

The known-answer vectors are the ones pinned in pengyos-attest's spec
(record schema and hash rule, fixed 2026-09-14): they prove the
witness's independent hash rule interoperates with the producer.
"""

from __future__ import annotations

import copy
import json

import pytest

from pengyos_verifier import attest
from pengyos_verifier.attest import (
    GENESIS_PREV_HASH,
    KAV_GENESIS,
    KAV_SECOND_HASH,
    canonical_json,
    record_hash,
    record_schema_errors,
    verify_attest_text,
)
from conftest import make_chain, make_record


# --- known-answer vectors --------------------------------------------------

def test_known_answer_vector_genesis():
    """The genesis record of pengyos-attest's spec verifies under our rule."""
    body = {k: v for k, v in KAV_GENESIS.items() if k != "hash"}
    assert record_hash(body) == KAV_GENESIS["hash"]
    assert record_hash(KAV_GENESIS) == KAV_GENESIS["hash"]  # hash key ignored
    assert record_schema_errors(KAV_GENESIS) == []


def test_known_answer_vector_second_record():
    second = make_record(
        seq=2, ts="2026-01-01T00:00:01Z", type_="event", data=None,
        prev_hash=KAV_GENESIS["hash"],
    )
    assert second["hash"] == KAV_SECOND_HASH


# --- canonical form --------------------------------------------------------

def test_canonical_json_is_sorted_compact_ascii():
    rec = {"b": 2, "a": "z\u00e9", "hash": "should-vanish"}
    assert canonical_json(rec) == '{"a":"z\\u00e9","b":2}'


def test_hash_is_invariant_to_key_order():
    a = {"seq": 1, "ts": "t", "type": "x", "data": None,
         "prev_hash": "0" * 64}
    b = dict(reversed(list(a.items())))
    assert record_hash(a) == record_hash(b)


def test_adding_any_key_invalidates_the_hash():
    a = {"seq": 1, "ts": "t", "type": "x", "data": None,
         "prev_hash": "0" * 64}
    b = dict(a, sig="extra")  # forward-compat: extra keys participate
    assert record_hash(a) != record_hash(b)


def test_mutating_data_invalidates_the_hash():
    a = {"seq": 1, "ts": "t", "type": "x", "data": {"k": 1},
         "prev_hash": "0" * 64}
    b = copy.deepcopy(a)
    b["data"]["k"] = 2
    assert record_hash(a) != record_hash(b)


# --- record schema ---------------------------------------------------------

def test_valid_record_has_no_schema_errors():
    assert record_schema_errors(KAV_GENESIS) == []


def test_missing_fields_are_reported_in_stable_order():
    errors = record_schema_errors({})
    assert errors == [
        "missing field 'seq'",
        "missing field 'ts'",
        "missing field 'type'",
        "missing field 'data'",
        "missing field 'prev_hash'",
        "missing field 'hash'",
    ]


def test_seq_must_be_a_real_int():
    rec = dict(KAV_GENESIS)
    rec["seq"] = True  # bool is an int in Python but NOT a valid seq
    assert any("seq must be an integer" in e for e in
               record_schema_errors(rec))
    rec["seq"] = 1.0
    assert any("seq must be an integer" in e for e in
               record_schema_errors(rec))
    rec["seq"] = "1"
    assert any("seq must be an integer" in e for e in
               record_schema_errors(rec))


def test_ts_format_is_pinned():
    for bad in ("2026-01-01 00:00:00Z", "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00", "yesterday", 42):
        rec = dict(KAV_GENESIS, ts=bad)
        assert any("ts must be" in e for e in record_schema_errors(rec)), bad
    # fractional seconds are allowed
    rec = dict(KAV_GENESIS, ts="2026-01-01T00:00:00.123Z")
    assert record_schema_errors(rec) == []


def test_type_must_be_nonempty_string():
    for bad in ("", 3, None):
        rec = dict(KAV_GENESIS, type=bad)
        assert any("type must be a non-empty string" in e for e in
                   record_schema_errors(rec))


def test_hashes_must_be_64_lowercase_hex():
    for bad in ("0" * 63, "0" * 64 + "0", "F" * 64, "g" * 64, 42, None):
        for field in ("prev_hash", "hash"):
            rec = dict(KAV_GENESIS, **{field: bad})
            assert any(
                f"{field} must be 64 lowercase hex chars" in e
                for e in record_schema_errors(rec)
            ), (field, bad)


def test_data_has_no_validity_rule():
    for data in (None, 0, [], {}, "s"):
        rec = dict(KAV_GENESIS, data=data)
        assert record_schema_errors(rec) == [], data


# --- chain walk ------------------------------------------------------------

def test_empty_and_blank_only_logs_are_valid_empty_chains():
    for text in ("", "\n", "\n\n"):
        report = verify_attest_text(text)
        assert report.ok is True
        assert report.record_count == 0
        assert report.reason is None
        assert report.first_bad_seq is None
        assert report.first_bad_line is None


def test_valid_chain_of_two_verifies():
    text = make_chain([
        {"ts": "2026-01-01T00:00:00Z", "type": "build",
         "data": {"commit": "abc123"}},
        {"ts": "2026-01-01T00:00:01Z", "type": "event", "data": None},
    ])
    report = verify_attest_text(text)
    assert report.ok is True
    assert report.record_count == 2
    # The genesis record of the first line is exactly the KAV record.
    first = json.loads(text.splitlines()[0])
    assert first["hash"] == KAV_GENESIS["hash"]


def test_blank_lines_are_skipped():
    good = make_chain([{"ts": "2026-01-01T00:00:00Z", "type": "build",
                        "data": None}])
    text = good + "\n\n  \n"
    assert verify_attest_text(text).ok is True


def test_non_json_line_is_reported_with_line_number():
    good = make_chain([{"ts": "2026-01-01T00:00:00Z", "type": "build",
                        "data": None}])
    report = verify_attest_text(good + "this is not json\n")
    assert report.ok is False
    assert report.record_count == 1
    assert report.first_bad_line == 2
    assert report.first_bad_seq is None
    assert report.reason == "not valid JSON"


def test_non_object_record_is_rejected():
    report = verify_attest_text("[1, 2, 3]\n")
    assert report.ok is False
    assert report.reason == "record is not a JSON object"
    assert report.first_bad_line == 1


def test_tampered_record_fails_self_hash_at_its_seq():
    recs = [
        {"ts": "2026-01-01T00:00:00Z", "type": "build", "data": None},
        {"ts": "2026-01-01T00:00:01Z", "type": "event", "data": None},
    ]
    text = make_chain(recs)
    lines = text.splitlines()
    second = json.loads(lines[1])
    second["data"] = {"tampered": True}
    lines[1] = json.dumps(second, sort_keys=True)
    report = verify_attest_text("\n".join(lines) + "\n")
    assert report.ok is False
    assert report.reason == "self-hash mismatch"
    assert report.first_bad_seq == 2
    assert report.first_bad_line == 2
    assert report.record_count == 1


def test_seq_gap_is_reported_with_the_offending_seq():
    first = make_record(1, "2026-01-01T00:00:00Z", "build", None,
                        GENESIS_PREV_HASH)
    # A well-formed record that skips to seq 3: self-hash is correct,
    # linkage is correct — only contiguity fails.
    gapped = make_record(3, "2026-01-01T00:00:02Z", "event", None,
                         first["hash"])
    text = (json.dumps(first, sort_keys=True) + "\n" +
            json.dumps(gapped, sort_keys=True) + "\n")
    report = verify_attest_text(text)
    assert report.ok is False
    assert report.reason == "seq not contiguous"
    assert report.first_bad_seq == 3
    assert report.first_bad_line == 2


def test_broken_linkage_is_reported():
    first = make_record(1, "2026-01-01T00:00:00Z", "build", None,
                        GENESIS_PREV_HASH)
    second = make_record(2, "2026-01-01T00:00:01Z", "event", None,
                         "f" * 64)  # wrong prev, hash recomputed to match
    second["hash"] = record_hash(second)
    text = (json.dumps(first, sort_keys=True) + "\n" +
            json.dumps(second, sort_keys=True) + "\n")
    report = verify_attest_text(text)
    assert report.ok is False
    assert report.reason == "prev_hash linkage broken"
    assert report.first_bad_seq == 2


def test_schema_error_carries_the_seq_when_parseable():
    rec = make_record(1, "2026-01-01T00:00:00Z", "build", None,
                      GENESIS_PREV_HASH)
    del rec["type"]  # schema-invalid, seq still present
    report = verify_attest_text(json.dumps(rec, sort_keys=True) + "\n")
    assert report.ok is False
    assert report.reason.startswith("schema: ")
    assert report.first_bad_seq == 1
    assert report.first_bad_line == 1


def test_reorder_fails():
    first = make_record(1, "2026-01-01T00:00:00Z", "build", None,
                        GENESIS_PREV_HASH)
    second = make_record(2, "2026-01-01T00:00:01Z", "event", None,
                         first["hash"])
    text = (json.dumps(second, sort_keys=True) + "\n" +
            json.dumps(first, sort_keys=True) + "\n")
    report = verify_attest_text(text)
    assert report.ok is False
    # The walk checks seq contiguity before prev_hash linkage: the
    # reordered second record claims seq 2 while the walk expects 1.
    assert report.reason == "seq not contiguous"
    assert report.first_bad_seq == 2
