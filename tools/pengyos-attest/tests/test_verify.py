"""Tests for the verify subtask.

Covers the spec's "Verification" section:

* append 3 records → verify true; chain links correct,
* edit a middle record → verify fails with the right seq,
* truncate the log → verify reports the lost tail when anchored, and the
  still-valid-prefix semantics hold unanchored,
* reorder records → verify fails,

plus the structured :class:`pengyos_attest.chain.VerifyReport` (first bad
seq / line, reasons) and the ``verify`` CLI subcommand end-to-end (exit 0/1).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pengyos_attest import chain
from pengyos_attest.chain import VerifyReport, verify_chain
from pengyos_attest.cli import EXIT_FAIL, EXIT_OK, main
from pengyos_attest.record import (
    GENESIS_PREV_HASH,
    new_record,
    record_hash,
    to_json_line,
)


def _make_chain(tmp_path: Path, n: int = 3, log: Path | None = None) -> list[dict]:
    """Append ``n`` records to ``log`` (default ``chain.jsonl``) and return them."""
    log = log or (tmp_path / "chain.jsonl")
    records = []
    for i in range(n):
        records.append(chain.append_event(
            log, "build", {"i": i}, ts=f"2026-01-01T00:00:0{i}Z"))
    return records


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _rewrite(log: Path, lines: list[str]) -> None:
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- the spec's verification scenarios -------------------------------------


def test_verify_three_appended_records_is_ok(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    report = verify_chain(log)
    assert report.ok is True
    assert report.record_count == 3
    assert report.first_bad_seq is None
    assert report.first_bad_line is None
    assert report.reason is None


def test_verify_missing_and_empty_log_are_ok(tmp_path):
    assert verify_chain(tmp_path / "nope.jsonl") == VerifyReport(True, 0)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert verify_chain(empty) == VerifyReport(True, 0)


def test_verify_empty_lines_are_ignored(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2, log)
    _rewrite(log, ["", _lines(log)[0], "", _lines(log)[1], ""])
    report = verify_chain(log)
    assert report.ok is True
    assert report.record_count == 2


def test_edit_middle_record_fails_with_right_seq(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    rec = json.loads(lines[1])
    rec["data"] = {"i": 999}  # tamper with record 2's payload
    _rewrite(log, [lines[0], to_json_line(rec), lines[2]])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 2
    assert report.first_bad_line == 2
    assert "hash mismatch" in report.reason


def test_edit_genesis_record_fails_with_seq_1(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2, log)
    lines = _lines(log)
    rec = json.loads(lines[0])
    rec["ts"] = "2099-01-01T00:00:00Z"
    _rewrite(log, [to_json_line(rec), lines[1]])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 1


def test_edit_last_record_fails_with_right_seq(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    rec = json.loads(lines[2])
    rec["type"] = "something-else"
    _rewrite(log, [lines[0], lines[1], to_json_line(rec)])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 3


def test_forged_hash_field_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2, log)
    lines = _lines(log)
    rec = json.loads(lines[1])
    rec["hash"] = "f" * 64  # a well-formed but wrong hash
    _rewrite(log, [lines[0], to_json_line(rec)])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 2
    assert "hash mismatch" in report.reason


def test_reorder_records_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    _rewrite(log, [lines[1], lines[0], lines[2]])  # swap 1 and 2
    report = verify_chain(log)
    assert report.ok is False
    # The record now sitting at position 1 is seq 2: that is the first bad seq.
    assert report.first_bad_seq == 2
    assert report.first_bad_line == 1
    assert "seq 2 where seq 1 is expected" in report.reason


def test_duplicate_record_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2, log)
    lines = _lines(log)
    _rewrite(log, [lines[0], lines[0]])  # seq 1 twice
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 1
    assert report.first_bad_line == 2


def test_gap_in_seq_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    _rewrite(log, [lines[0], lines[2]])  # drop record 2
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 3
    assert "where seq 2 is expected" in report.reason


def test_unparseable_line_fails_with_line_number(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2, log)
    lines = _lines(log)
    _rewrite(log, [lines[0], "this is not json", lines[1]])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq is None
    assert report.first_bad_line == 2
    assert "not valid JSON" in report.reason


def test_schema_invalid_line_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1, log)
    lines = _lines(log)
    _rewrite(log, [lines[0], '{"seq": 2}'])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 2
    assert report.first_bad_line == 2
    assert "missing required field" in report.reason


def test_non_object_json_line_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1, log)
    lines = _lines(log)
    _rewrite(log, [lines[0], "[1, 2, 3]"])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq is None
    assert report.first_bad_line == 2


def test_first_bad_report_is_the_first_finding(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    broken1 = json.loads(lines[0])
    broken1["data"] = "tampered"
    broken2 = json.loads(lines[2])
    broken2["type"] = "tampered"
    _rewrite(log, [to_json_line(broken1), lines[1], to_json_line(broken2)])
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 1  # the EARLIER record is reported
    assert report.first_bad_line == 1


# --- truncation: anchored vs. still-valid-prefix ---------------------------


def test_truncation_unanchored_is_still_valid_prefix(tmp_path):
    """Documented semantics: without an anchor, a self-consistent prefix (a
    truncated chain) verifies OK — the lost tail is undetectable without an
    externally committed head hash."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    _rewrite(log, lines[:2])  # lose record 3
    report = verify_chain(log)
    assert report.ok is True
    assert report.record_count == 2


def test_truncation_anchored_reports_lost_tail(tmp_path):
    log = tmp_path / "chain.jsonl"
    records = _make_chain(tmp_path, 3, log)
    anchor = records[2]["hash"]
    lines = _lines(log)
    _rewrite(log, lines[:2])  # lose record 3
    report = verify_chain(log, anchor=anchor)
    assert report.ok is False
    assert report.record_count == 2
    assert report.first_bad_seq is None
    assert "truncated" in report.reason
    assert "records after seq 2 were lost" in report.reason


def test_anchor_matching_head_is_ok(tmp_path):
    log = tmp_path / "chain.jsonl"
    records = _make_chain(tmp_path, 3, log)
    report = verify_chain(log, anchor=records[2]["hash"])
    assert report.ok is True
    assert report.record_count == 3


def test_anchor_on_empty_chain_fails(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    report = verify_chain(empty, anchor="a" * 64)
    assert report.ok is False
    assert report.record_count == 0
    assert "truncated" in report.reason


def test_anchor_ignores_a_tampered_prefix(tmp_path):
    """A tampered middle record fails the walk BEFORE the anchor is consulted."""
    log = tmp_path / "chain.jsonl"
    records = _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    rec = json.loads(lines[1])
    rec["data"] = "evil"
    _rewrite(log, [lines[0], to_json_line(rec), lines[2]])
    report = verify_chain(log, anchor=records[2]["hash"])
    assert report.ok is False
    assert report.first_bad_seq == 2
    assert "hash mismatch" in report.reason


def test_anchor_to_an_other_chain_fails(tmp_path):
    """An anchor from a DIFFERENT chain (different payloads) is a mismatch."""
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2, log)
    other = tmp_path / "other.jsonl"
    other_records = [
        chain.append_event(other, "deploy", {"i": 100},
                           ts="2026-01-01T00:00:00Z"),
        chain.append_event(other, "deploy", {"i": 101},
                           ts="2026-01-01T00:00:01Z"),
    ]
    report = verify_chain(log, anchor=other_records[1]["hash"])
    assert report.ok is False
    assert "truncated" in report.reason


# --- linkage checks ---------------------------------------------------------


def test_genesis_prev_hash_must_be_genesis_value(tmp_path):
    log = tmp_path / "chain.jsonl"
    first = chain.append_event(log, "event", None, ts="2026-01-01T00:00:00Z")
    tampered = copy.deepcopy(first)
    tampered["prev_hash"] = "ab" * 32
    # prev_hash participates in the hash payload, so the self-hash now fails.
    log.write_text(to_json_line(tampered), encoding="utf-8")
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 1
    assert "hash mismatch" in report.reason


def test_unlinked_second_record_fails(tmp_path):
    log = tmp_path / "chain.jsonl"
    first = chain.append_event(log, "event", None, ts="2026-01-01T00:00:00Z")
    # A fresh genesis-linked record at seq 2: self-hash fine, linkage broken.
    orphan = new_record(2, "2026-01-01T00:00:01Z", "event", None,
                        prev_hash=GENESIS_PREV_HASH)
    log.write_text(to_json_line(first) + "\n" + to_json_line(orphan),
                   encoding="utf-8")
    report = verify_chain(log)
    assert report.ok is False
    assert report.first_bad_seq == 2
    assert "does not link" in report.reason
    assert "(chain broken)" in report.reason


def test_extra_keys_are_tolerated_when_consistent(tmp_path):
    """Forward-compat: extra keys don't break verify as long as the record's
    own hash was computed including them."""
    log = tmp_path / "chain.jsonl"
    first = chain.append_event(log, "event", None, ts="2026-01-01T00:00:00Z")
    rec = copy.deepcopy(first)
    rec["seq"] = 2
    rec["ts"] = "2026-01-01T00:00:01Z"
    rec["prev_hash"] = first["hash"]
    rec["extra"] = "fine"
    rec["hash"] = record_hash(rec)
    log.write_text(to_json_line(first) + "\n" + to_json_line(rec),
                   encoding="utf-8")
    assert verify_chain(log).ok is True


def test_record_count_counts_failing_lines(tmp_path):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 4, log)
    lines = _lines(log)
    _rewrite(log, [lines[0], "garbage", lines[2], lines[3]])
    report = verify_chain(log)
    assert report.ok is False
    assert report.record_count == 2  # one good line + the failing one


# --- CLI --------------------------------------------------------------------


def test_cli_verify_ok_exit_zero(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    rc = main(["verify", "--log", str(log)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "OK: chain verified (3 records)" in out


def test_cli_verify_ok_singular_record(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1, log)
    rc = main(["verify", "--log", str(log)])
    assert rc == EXIT_OK
    assert "1 record)" in capsys.readouterr().out


def test_cli_verify_missing_log_is_ok(tmp_path, capsys):
    rc = main(["verify", "--log", str(tmp_path / "nope.jsonl")])
    assert rc == EXIT_OK
    assert "0 records" in capsys.readouterr().out


def test_cli_verify_tamper_exit_one_reports_seq(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    rec = json.loads(lines[1])
    rec["data"] = "tampered"
    _rewrite(log, [lines[0], to_json_line(rec), lines[2]])
    rc = main(["verify", "--log", str(log)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "FAIL:" in err
    assert "first bad seq=2" in err
    assert "line 2" in err


def test_cli_verify_reorder_exit_one(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 2, log)
    lines = _lines(log)
    _rewrite(log, [lines[1], lines[0]])
    rc = main(["verify", "--log", str(log)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "first bad seq=2" in err
    assert "line 1" in err


def test_cli_verify_anchor_detects_truncation(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    records = _make_chain(tmp_path, 3, log)
    lines = _lines(log)
    _rewrite(log, lines[:2])
    rc = main(["verify", "--log", str(log), "--anchor", records[2]["hash"]])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "truncated" in err
    assert "records after seq 2 were lost" in err


def test_cli_verify_anchor_ok(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    records = _make_chain(tmp_path, 3, log)
    rc = main(["verify", "--log", str(log), "--anchor", records[2]["hash"]])
    assert rc == EXIT_OK
    capsys.readouterr()


def test_cli_verify_unparseable_line_reports_line_not_seq(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    _make_chain(tmp_path, 1, log)
    log.write_text(_lines(log)[0] + "\nnot json\n", encoding="utf-8")
    rc = main(["verify", "--log", str(log)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "line 2" in err
    assert "first bad seq" not in err


def test_cli_verify_unreadable_log_is_operation_failure(tmp_path, capsys):
    # A directory is not readable as a log: OSError path → exit 1, not a crash.
    rc = main(["verify", "--log", str(tmp_path)])
    assert rc == EXIT_FAIL
    assert "cannot read log" in capsys.readouterr().err
