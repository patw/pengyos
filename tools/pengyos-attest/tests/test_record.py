"""Tests for the record schema + canonical JSON serialization (item 2).

Covers:

* the exact field set and types of a record,
* the hash rule ``hash = sha256(prev_hash || canonical(record-without-hash))``,
  pinned by known-answer vectors,
* canonical JSON determinism (sorted keys, compact, ASCII),
* ``new_record`` building self-consistent records (incl. genesis),
* ``validate_record`` rejecting every schema violation and chain-linkage break,
* JSONL line round-trips.
"""

import json

import pytest

from pengyos_attest.record import (
    GENESIS_PREV_HASH,
    RECORD_FIELDS,
    RecordError,
    canonical_json,
    canonical_record,
    from_json_line,
    new_record,
    record_hash,
    to_json_line,
    validate_record,
)

# --- known-answer vectors ---------------------------------------------------
# Pinned so any change to the hash rule or canonicalization is a loud, visible
# break (these are the spec's reproducibility anchors).
GENESIS_TS = "2026-01-01T00:00:00Z"

GENESIS_RECORD = {
    "seq": 1,
    "ts": GENESIS_TS,
    "type": "build",
    "data": {"commit": "abc123"},
    "prev_hash": GENESIS_PREV_HASH,
}
GENESIS_HASH = "5d133068004e620a7c8da3d15b1b927b5aae0ff08e6361452b98e9d674832347"

SECOND_RECORD = {
    "seq": 2,
    "ts": "2026-01-01T00:00:01Z",
    "type": "event",
    "data": None,
    "prev_hash": GENESIS_HASH,
}
SECOND_HASH = "38eb473499a5f12025106c37f875ace960bdc064a07a320251c0436b67d02882"


# --- canonical JSON serialization -------------------------------------------


def test_canonical_json_sorts_keys_compactly_and_escapes_ascii():
    value = {"b": [1, 2], "a": {"z": "\u00e9", "y": None}}
    assert canonical_json(value) == '{"a":{"y":null,"z":"\\u00e9"},"b":[1,2]}'


def test_canonical_json_is_independent_of_insertion_order():
    assert canonical_json({"x": 1, "y": 2}) == canonical_json({"y": 2, "x": 1})


def test_canonical_record_excludes_only_the_hash_field():
    record = dict(GENESIS_RECORD, hash="f" * 64, extra="kept")
    body = json.loads(canonical_record(record))
    assert "hash" not in body
    assert body["extra"] == "kept"
    assert body["seq"] == 1


def test_canonical_record_rejects_non_dict():
    with pytest.raises(RecordError, match="JSON object"):
        canonical_record(["not", "a", "record"])


# --- hash rule ---------------------------------------------------------------


def test_known_answer_genesis_hash():
    assert record_hash(GENESIS_RECORD) == GENESIS_HASH


def test_known_answer_second_record_hash():
    assert record_hash(SECOND_RECORD) == SECOND_HASH


def test_hash_ignores_the_hash_field_itself():
    """Changing (or removing) 'hash' must not change the computed hash."""
    with_hash = dict(GENESIS_RECORD, hash="f" * 64)
    assert record_hash(with_hash) == record_hash(GENESIS_RECORD) == GENESIS_HASH


def test_hash_depends_on_every_other_field():
    base = record_hash(GENESIS_RECORD)
    for field in ("seq", "ts", "type", "data", "prev_hash"):
        mutated = dict(GENESIS_RECORD)
        mutated[field] = {"seq": 2, "ts": "2026-01-01T00:00:02Z",
                          "type": "other", "data": 42,
                          "prev_hash": "a" * 64}[field]
        assert record_hash(mutated) != base, field


def test_hash_key_order_is_irrelevant():
    reordered = {k: GENESIS_RECORD[k] for k in ("type", "prev_hash", "data", "seq", "ts")}
    assert record_hash(reordered) == GENESIS_HASH


def test_record_hash_requires_prev_hash_string():
    with pytest.raises(RecordError, match="prev_hash"):
        record_hash({"seq": 1, "ts": GENESIS_TS, "type": "t", "data": None})


# --- new_record --------------------------------------------------------------


def test_new_record_builds_self_consistent_genesis():
    record = new_record(1, GENESIS_TS, "build", {"commit": "abc123"})
    assert record == {**GENESIS_RECORD, "hash": GENESIS_HASH}
    validate_record(record)


def test_new_record_links_to_previous_hash():
    genesis = new_record(1, GENESIS_TS, "build", {"commit": "abc123"})
    second = new_record(2, "2026-01-01T00:00:01Z", "event", None,
                        prev_hash=genesis["hash"])
    assert second == {**SECOND_RECORD, "hash": SECOND_HASH}
    validate_record(second, expected_seq=2, expected_prev_hash=genesis["hash"])


def test_new_record_defaults_to_genesis_prev_hash():
    record = new_record(1, GENESIS_TS, "event", None)
    assert record["prev_hash"] == GENESIS_PREV_HASH


def test_new_record_rejects_bad_inputs():
    with pytest.raises(RecordError, match="seq"):
        new_record(0, GENESIS_TS, "event", None)
    with pytest.raises(RecordError, match="ts"):
        new_record(1, "2026-01-01 00:00:00", "event", None)  # no T, no Z
    with pytest.raises(RecordError, match="type"):
        new_record(1, GENESIS_TS, "", None)
    with pytest.raises(RecordError, match="prev_hash"):
        new_record(1, GENESIS_TS, "event", None, prev_hash="xyz")
    with pytest.raises(RecordError, match="seq"):
        new_record(True, GENESIS_TS, "event", None)  # bool is not a seq


def test_new_record_accepts_fractional_second_timestamp():
    record = new_record(1, "2026-01-01T00:00:00.123Z", "event", None)
    validate_record(record)


def test_new_record_supports_any_json_payload():
    for data in (None, 42, 1.5, "s", True, [1, {"n": 2}, None], {"a": ["raw"]}, {}):
        record = new_record(1, GENESIS_TS, "event", data)
        validate_record(record)
    with pytest.raises(RecordError, match="not JSON-serializable"):
        new_record(1, GENESIS_TS, "event", object())


# --- validate_record ---------------------------------------------------------


def _good() -> dict:
    return {**GENESIS_RECORD, "hash": GENESIS_HASH}


def test_validate_accepts_good_record_and_tolerates_extra_keys():
    validate_record(_good())
    # Extra keys are allowed, but they participate in the hash: a record is
    # valid only if its hash was computed *including* the extra.
    signed = {**_good(), "sig": "abc"}
    signed["hash"] = record_hash(signed)
    validate_record(signed)
    # ...and changing an extra invalidates the hash.
    with pytest.raises(RecordError, match="hash mismatch"):
        validate_record({**signed, "sig": "tampered"})


@pytest.mark.parametrize("field", RECORD_FIELDS)
def test_validate_rejects_missing_field(field):
    bad = _good()
    del bad[field]
    with pytest.raises(RecordError, match=f"missing required field {field!r}"):
        validate_record(bad)


def test_validate_rejects_wrong_types():
    cases = [
        ("seq", "1"),
        ("seq", True),
        ("seq", 1.0),
        ("ts", 12345),
        ("type", None),
        ("prev_hash", 0),
        ("hash", b"00"),
    ]
    for field, value in cases:
        bad = _good()
        bad[field] = value
        with pytest.raises(RecordError):
            validate_record(bad)


def test_validate_rejects_malformed_hex_fields():
    for field in ("prev_hash", "hash"):
        for value in ("ABC" * 22, "0" * 63, "0" * 65, "g" * 64):
            bad = _good()
            bad[field] = value
            with pytest.raises(RecordError, match=field):
                validate_record(bad)


def test_validate_rejects_bad_timestamps():
    # The spec pins the *format* (ISO-8601 UTC, 'Z' suffix); calendar
    # plausibility (month <= 12) is deliberately out of scope, so only
    # format violations are tested here.
    for ts in ("2026-01-01T00:00:00", "2026-01-01T00:00:00+00:00",
               "2026-01-01 00:00:00Z", "not-a-time"):
        bad = _good()
        bad["ts"] = ts
        with pytest.raises(RecordError, match="ts"):
            validate_record(bad)


def test_validate_detects_tampered_hash_and_tampered_fields():
    bad = _good()
    bad["hash"] = "e" * 64
    with pytest.raises(RecordError, match="hash mismatch"):
        validate_record(bad)
    bad = _good()
    bad["data"] = {"commit": "evil"}
    with pytest.raises(RecordError, match="hash mismatch"):
        validate_record(bad)


def test_validate_checks_expected_seq_and_prev_hash():
    with pytest.raises(RecordError, match="seq 1 != expected 2"):
        validate_record(_good(), expected_seq=2)
    with pytest.raises(RecordError, match="prev_hash"):
        validate_record(_good(), expected_prev_hash="f" * 64)
    validate_record(_good(), expected_seq=1, expected_prev_hash=GENESIS_PREV_HASH)


def test_validate_rejects_non_dict_record():
    for bad in ([1, 2], "string", None, 42):
        with pytest.raises(RecordError, match="JSON object"):
            validate_record(bad)


# --- JSONL round-trip --------------------------------------------------------


def test_json_line_round_trip():
    record = new_record(1, GENESIS_TS, "build", {"commit": "abc123"},
                        prev_hash=GENESIS_PREV_HASH)
    line = to_json_line(record)
    assert "\n" not in line
    parsed = from_json_line(line + "\n")  # trailing newline is tolerated
    assert parsed == record
    # Deterministic: same record -> same line bytes.
    assert to_json_line(parsed) == line


def test_json_line_is_sorted_compact_ascii():
    record = new_record(1, GENESIS_TS, "build", {"z": "é", "a": 1})
    line = to_json_line(record)
    assert line == json.dumps(record, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True)
    assert "\\u00e9" in line


def test_from_json_line_rejects_invalid_json_and_bad_records():
    with pytest.raises(RecordError, match="not valid JSON"):
        from_json_line("this is not json")
    with pytest.raises(RecordError, match="missing required field"):
        from_json_line('{"seq": 1}')
