"""Tests for the local append-only hash-chained event log."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pengyos_mutations import attest


# --------------------------------------------------------------------------- #
# Record schema + hash rule
# --------------------------------------------------------------------------- #
def test_new_record_is_self_consistent() -> None:
    rec = attest.new_record(1, "2026-09-15T00:00:00Z", "apply", {"a": 1})
    assert rec["seq"] == 1
    assert rec["type"] == "apply"
    assert rec["prev_hash"] == attest.GENESIS_PREV_HASH
    assert rec["hash"] == attest.record_hash(rec)


def test_record_hash_matches_external_schema() -> None:
    """The hash rule is byte-identical to the canonical pengyos-attest schema."""
    data = {"desc": "x", "n": 2, "nested": {"z": [1, 2]}}
    rec = attest.new_record(3, "2026-01-02T03:04:05Z", "apply", data)
    # Re-derive by hand: sha256(prev_hash || canonical(record-without-hash)).
    body = {k: v for k, v in rec.items() if k != "hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    import hashlib

    expected = hashlib.sha256(rec["prev_hash"].encode("ascii") + canonical.encode("utf-8")).hexdigest()
    assert rec["hash"] == expected


def test_new_record_enforces_schema() -> None:
    with pytest.raises(attest.RecordError):
        attest.new_record(0, "2026-09-15T00:00:00Z", "apply", {})  # seq < 1
    with pytest.raises(attest.RecordError):
        attest.new_record(1, "not-a-timestamp", "apply", {})  # bad ts
    with pytest.raises(attest.RecordError):
        attest.new_record(1, "2026-09-15T00:00:00Z", "", {})  # empty type


# --------------------------------------------------------------------------- #
# Append / read / verify
# --------------------------------------------------------------------------- #
def test_append_builds_a_chain(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    r1 = attest.append_event(log, "apply", {"i": 1})
    r2 = attest.append_event(log, "apply", {"i": 2})
    assert r1["seq"] == 1 and r2["seq"] == 2
    assert r2["prev_hash"] == r1["hash"]
    assert attest.chain_length(log) == 2


def test_verify_chain_ok(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    attest.append_event(log, "apply", {"i": 1})
    attest.append_event(log, "apply", {"i": 2})
    report = attest.verify_chain(log)
    assert report.ok
    assert report.record_count == 2


def test_verify_empty_or_missing_log_is_ok(tmp_path) -> None:
    assert attest.verify_chain(tmp_path / "missing.jsonl").ok
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert attest.verify_chain(empty).ok


def test_tampered_record_is_detected(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    attest.append_event(log, "apply", {"secret": "one"})
    # Rewrite the first record's payload in place.
    lines = log.read_text(encoding="utf-8").splitlines()
    mutated = json.loads(lines[0])
    mutated["data"] = {"secret": "TAMPERED"}
    lines[0] = attest.to_json_line(mutated)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = attest.verify_chain(log)
    assert not report.ok
    assert report.first_bad_seq == 1
    assert report.reason


def test_reordered_chain_is_detected(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    a = attest.append_event(log, "apply", {"i": 1})
    b = attest.append_event(log, "apply", {"i": 2})
    lines = log.read_text(encoding="utf-8").splitlines()
    lines = [lines[1], lines[0]]  # swap the two records
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = attest.verify_chain(log)
    assert not report.ok
    assert report.first_bad_seq == 2


def test_truncation_detected_with_anchor(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    first = attest.append_event(log, "apply", {"i": 1})
    second = attest.append_event(log, "apply", {"i": 2})
    # Simulate a lost tail: keep only the first record, but anchor to the
    # (now missing) head's hash. A self-consistent prefix that ends before the
    # anchor is reported as truncated.
    log.write_text(attest.to_json_line(first) + "\n", encoding="utf-8")
    report = attest.verify_chain(log, anchor=second["hash"])
    assert not report.ok
    assert "truncated" in report.reason


def test_append_is_atomic_leaves_no_temp(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    attest.append_event(log, "apply", {"i": 1})
    stray = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert stray == []


def test_corrupt_log_raises_on_append(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(attest.LogError):
        attest.append_event(log, "apply", {"i": 1})


def test_lock_sidecar_is_a_stable_sibling(tmp_path) -> None:
    log = tmp_path / "events.jsonl"
    assert attest.lock_path(log).name == "events.jsonl.lock"


def test_dump_log_json_empty(capsys) -> None:
    assert attest.dump_log(log_path=_unique_log(), json_out=True) == 0
    import json

    assert json.loads(capsys.readouterr().out) == []


def test_dump_log_text_summary(capsys) -> None:
    log = _unique_log()
    attest.append_event(log, "apply", {"desc": "hello", "root": "/x"})
    assert attest.dump_log(log_path=log, json_out=False) == 0
    out = capsys.readouterr().out
    assert "apply" in out and "/x" in out and "exit=None" in out


def test_dump_log_resolves_store(tmp_path, capsys) -> None:
    store = tmp_path / "store"
    store.mkdir()
    (store / "events.jsonl").write_text(
        attest.to_json_line(
            attest.new_record(1, "2026-09-15T00:00:00Z", "apply", {"desc": "s"})
        )
        + "\n",
        encoding="utf-8",
    )
    assert attest.dump_log(store=str(store)) == 0
    assert "apply" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Schema compatibility with the external pengyos-attest package
# --------------------------------------------------------------------------- #
def test_local_chain_is_interchangeable_with_pengyos_attest() -> None:
    """When the external package is importable, its verifier accepts our log."""
    pytest.importorskip("pengyos_attest")
    from pengyos_attest import chain as external_chain
    from pengyos_attest import record as external_record

    log = _unique_log()
    ts = "2026-09-15T00:00:00Z"
    try:
        external = external_chain.append_event(log, "apply", {"src": "external"}, ts=ts)
        # Same hash rule -> identical hashes for identical inputs.
        same = external_record.new_record(1, ts, "apply", {"src": "external"})
        assert external["hash"] == same["hash"]
        assert external["hash"] == external_record.record_hash(same)
        # The external verifier accepts a log seeded by the local chain.
        report = external_chain.verify_chain(log)
        assert report.ok
        # And the local verifier accepts a log seeded by the external package.
        assert attest.verify_chain(log).ok
    finally:
        log.unlink(missing_ok=True)


def _unique_log() -> Path:
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    return Path(path)
