"""Tests for the append subtask.

Covers:

* :func:`pengyos_attest.chain.append_event` — building the next record from the
  last (genesis vs. continuation), correct ``prev_hash`` / ``seq`` linkage,
* atomic + crash-safe write (no stray temp files; the file is always valid),
* :func:`read_records` / :func:`last_record` / :func:`chain_length`,
* rejection of a corrupt existing log (:class:`LogError`),
* the ``append`` CLI subcommand end-to-end (exit codes 0/1/2, stdout line).
"""

import json
from pathlib import Path

import pytest

from pengyos_attest import chain
from pengyos_attest.chain import chain_length
from pengyos_attest.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, main
from pengyos_attest.record import (
    GENESIS_PREV_HASH,
    RecordError,
    from_json_line,
    to_json_line,
    validate_record,
)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# --- genesis / continuation -------------------------------------------------


def test_append_to_missing_file_starts_genesis(tmp_path):
    log = tmp_path / "chain.jsonl"
    record = chain.append_event(log, "build", {"commit": "abc123"},
                                ts="2026-01-01T00:00:00Z")
    assert record["seq"] == 1
    assert record["prev_hash"] == GENESIS_PREV_HASH
    # The single line on disk is exactly the record, canonical + newline-terminated.
    assert _lines(log) == [to_json_line(record)]
    validate_record(record, expected_seq=1, expected_prev_hash=GENESIS_PREV_HASH)


def test_append_continues_chain_from_last_record(tmp_path):
    log = tmp_path / "chain.jsonl"
    first = chain.append_event(log, "build", {"commit": "abc123"},
                               ts="2026-01-01T00:00:00Z")
    second = chain.append_event(log, "event", None,
                                ts="2026-01-01T00:00:01Z")
    assert second["seq"] == 2
    assert second["prev_hash"] == first["hash"]
    validate_record(second, expected_seq=2, expected_prev_hash=first["hash"])
    # Disk holds both, in order, and the on-disk second line matches the record.
    lines = _lines(log)
    assert len(lines) == 2
    assert from_json_line(lines[1]) == second


def test_append_three_records_forms_linked_chain(tmp_path):
    log = tmp_path / "chain.jsonl"
    recs = [
        chain.append_event(log, "build", {"commit": "a"}, ts="2026-01-01T00:00:00Z"),
        chain.append_event(log, "event", None, ts="2026-01-01T00:00:01Z"),
        chain.append_event(log, "build", {"commit": "c"}, ts="2026-01-01T00:00:02Z"),
    ]
    assert chain_length(log) == 3
    # Contiguous seq + correct linkage between consecutive records.
    prev = GENESIS_PREV_HASH
    for i, rec in enumerate(recs, start=1):
        validate_record(rec, expected_seq=i, expected_prev_hash=prev)
        prev = rec["hash"]


def test_append_empty_file_starts_genesis(tmp_path):
    log = tmp_path / "chain.jsonl"
    log.write_text("", encoding="utf-8")
    record = chain.append_event(log, "event", None)
    assert record["seq"] == 1
    assert record["prev_hash"] == GENESIS_PREV_HASH


def test_append_ignores_trailing_blank_lines(tmp_path):
    log = tmp_path / "chain.jsonl"
    first = chain.append_event(log, "event", None, ts="2026-01-01T00:00:00Z")
    # A trailing blank line must not be mistaken for the head.
    log.write_text(to_json_line(first) + "\n\n", encoding="utf-8")
    second = chain.append_event(log, "event", None, ts="2026-01-01T00:00:01Z")
    assert second["seq"] == 2
    assert second["prev_hash"] == first["hash"]


@pytest.mark.parametrize("data", [None, 42, 1.5, "s", True, [1, 2], {"a": ["x"]}])
def test_append_supports_any_json_payload(tmp_path, data):
    log = tmp_path / "chain.jsonl"
    record = chain.append_event(log, "event", data)
    assert record["data"] == data
    validate_record(record)


def test_append_defaults_ts_to_valid_utc_now(tmp_path):
    log = tmp_path / "chain.jsonl"
    record = chain.append_event(log, "event", None)  # no explicit ts
    validate_record(record)  # ts format is validated here


def test_append_rejects_non_serializable_data(tmp_path):
    with pytest.raises(RecordError, match="not JSON-serializable"):
        chain.append_event(tmp_path / "c.jsonl", "event", object())


def test_append_rejects_bad_type(tmp_path):
    with pytest.raises(RecordError, match="type"):
        chain.append_event(tmp_path / "c.jsonl", "", None)


# --- corrupt log rejection --------------------------------------------------


def test_append_to_corrupt_log_raises_logerror(tmp_path):
    log = tmp_path / "chain.jsonl"
    log.write_text("this is not json\n", encoding="utf-8")
    with pytest.raises(chain.LogError, match="not a valid record"):
        chain.append_event(log, "event", None)


def test_append_to_log_with_bad_last_record_raises(tmp_path):
    log = tmp_path / "chain.jsonl"
    good = chain.append_event(log, "event", None, ts="2026-01-01T00:00:00Z")
    # A well-formed first line followed by a schema-invalid last line.
    log.write_text(to_json_line(good) + "\n" + '{"seq": 2}\n', encoding="utf-8")
    with pytest.raises(chain.LogError, match="line 2"):
        chain.append_event(log, "event", None)


# --- readers ---------------------------------------------------------------


def test_read_records_missing_and_empty(tmp_path):
    assert chain.read_records(tmp_path / "nope.jsonl") == []
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert chain.read_records(empty) == []
    assert chain.last_record(empty) is None
    assert chain.chain_length(empty) == 0


def test_chain_length_and_last_record(tmp_path):
    log = tmp_path / "chain.jsonl"
    first = chain.append_event(log, "event", 1, ts="2026-01-01T00:00:00Z")
    second = chain.append_event(log, "event", 2, ts="2026-01-01T00:00:01Z")
    assert chain.chain_length(log) == 2
    assert chain.last_record(log) == second
    assert chain.last_record(tmp_path / "nope.jsonl") is None


# --- atomic / crash-safe write ---------------------------------------------


def test_atomic_write_leaves_no_stray_temp_files(tmp_path):
    log = tmp_path / "chain.jsonl"
    for i in range(3):
        chain.append_event(log, "event", i)
    # The log (plus its expected concurrency-lock sidecar, item 6) remains;
    # there is NO *.tmp debris from the atomic-write temp files.
    remaining = list(tmp_path.iterdir())
    assert log in remaining
    assert chain.lock_path(log) in remaining  # expected .lock sidecar
    assert not any(p.name.endswith(".tmp") for p in remaining)


def test_append_always_leaves_a_valid_file(tmp_path):
    log = tmp_path / "chain.jsonl"
    for i in range(5):
        chain.append_event(log, "event", i)
        # After every append the file is a fully valid, link-consistent chain.
        records = chain.read_records(log)
        assert len(records) == i + 1
        prev = GENESIS_PREV_HASH
        for j, rec in enumerate(records, start=1):
            validate_record(rec, expected_seq=j, expected_prev_hash=prev)
            prev = rec["hash"]


# --- CLI append ------------------------------------------------------------


def test_cli_append_two_records_exit_ok(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    rc1 = main(["append", "--log", str(log), "--type", "build",
                "--data", '{"commit": "abc123"}'])
    out1 = capsys.readouterr().out
    assert rc1 == EXIT_OK
    assert "seq=1" in out1
    rc2 = main(["append", "--log", str(log), "--type", "event", "--data", "null"])
    out2 = capsys.readouterr().out
    assert rc2 == EXIT_OK
    assert "seq=2" in out2
    assert chain.chain_length(log) == 2


def test_cli_append_defaults_type_and_null_data(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    rc = main(["append", "--log", str(log)])
    assert rc == EXIT_OK
    capsys.readouterr()
    (record,) = chain.read_records(log)
    assert record["type"] == "event"
    assert record["data"] is None


def test_cli_append_malformed_data_is_usage_error(tmp_path, capsys):
    rc = main(["append", "--log", str(tmp_path / "c.jsonl"), "--data", "{not json"])
    assert rc == EXIT_USAGE
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_append_bad_type_is_usage_error(tmp_path, capsys):
    rc = main(["append", "--log", str(tmp_path / "c.jsonl"), "--type", ""])
    assert rc == EXIT_USAGE
    assert "type" in capsys.readouterr().err


def test_cli_append_corrupt_log_is_operation_failure(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    log.write_text("garbage\n", encoding="utf-8")
    rc = main(["append", "--log", str(log)])
    assert rc == EXIT_FAIL
    assert "not a valid record" in capsys.readouterr().err
