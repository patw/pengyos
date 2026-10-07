"""Tests for the show subtask.

Covers:

* pretty format (``seq=N ts=… type=…`` / ``hash:`` / ``data:`` blocks),
* ``--json`` (one compact JSON line per record, byte-identical to the stored
  line for valid records),
* ``--tail N`` (including ``N`` larger than the chain, and ``N < 1`` as a
  usage error),
* a missing/empty log is a valid empty chain (no output, exit 0),
* a corrupt log is an operation failure (exit 1).
"""

import json
from pathlib import Path

from pengyos_attest import chain
from pengyos_attest.cli import EXIT_FAIL, EXIT_OK, EXIT_USAGE, main
from pengyos_attest.record import canonical_json

TS = "2026-01-01T00:00:0"  # ts for record i is f"{TS}{i}Z"


def _build_chain(tmp_path: Path, n: int = 3) -> Path:
    """A fresh n-record chain with distinct types and payloads."""
    log = tmp_path / "chain.jsonl"
    for i in range(1, n + 1):
        chain.append_event(log, f"t{i}", {"i": i}, ts=f"{TS}{i}Z")
    return log


# --- pretty format ----------------------------------------------------------


def test_show_pretty_missing_log_is_empty_chain(tmp_path, capsys):
    rc = main(["show", "--log", str(tmp_path / "nope.jsonl")])
    out, err = capsys.readouterr()
    assert rc == EXIT_OK
    assert out == ""
    assert err == ""


def test_show_pretty_empty_log_is_empty_chain(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    log.write_text("", encoding="utf-8")
    rc = main(["show", "--log", str(log)])
    out, err = capsys.readouterr()
    assert rc == EXIT_OK
    assert out == ""
    assert err == ""


def test_show_pretty_one_record(tmp_path, capsys):
    log = _build_chain(tmp_path, 1)
    (rec,) = chain.read_records(log)
    rc = main(["show", "--log", str(log)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert out == (
        f"seq=1 ts={rec['ts']} type={rec['type']}\n"
        f"  hash: {rec['hash']}\n"
        f"  data: {canonical_json(rec['data'])}\n"
    )


def test_show_pretty_three_records_in_order(tmp_path, capsys):
    log = _build_chain(tmp_path, 3)
    recs = chain.read_records(log)
    rc = main(["show", "--log", str(log)])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    lines = out.splitlines()
    # Exactly three 3-line blocks, in seq order.
    assert len(lines) == 9
    for i, rec in enumerate(recs, start=1):
        assert lines[3 * (i - 1)] == f"seq={i} ts={rec['ts']} type={rec['type']}"
        assert lines[3 * (i - 1) + 1] == f"  hash: {rec['hash']}"
        assert lines[3 * (i - 1) + 2] == f"  data: {canonical_json(rec['data'])}"


# --- --json -----------------------------------------------------------------


def test_show_json_byte_identical_to_stored_lines(tmp_path, capsys):
    log = _build_chain(tmp_path, 3)
    stored = log.read_text(encoding="utf-8").splitlines()
    rc = main(["show", "--log", str(log), "--json"])
    out_lines = capsys.readouterr().out.splitlines()
    assert rc == EXIT_OK
    assert out_lines == stored
    # And each line is a valid, parseable record.
    for line in out_lines:
        rec = json.loads(line)
        assert "seq" in rec and "hash" in rec


def test_show_json_tail_combo(tmp_path, capsys):
    log = _build_chain(tmp_path, 3)
    stored = log.read_text(encoding="utf-8").splitlines()
    rc = main(["show", "--log", str(log), "--json", "--tail", "1"])
    out_lines = capsys.readouterr().out.splitlines()
    assert rc == EXIT_OK
    assert out_lines == stored[-1:]


# --- --tail -----------------------------------------------------------------


def test_show_tail_two_of_three(tmp_path, capsys):
    log = _build_chain(tmp_path, 3)
    rc = main(["show", "--log", str(log), "--tail", "2"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "seq=1" not in out
    assert "seq=2" in out and "seq=3" in out
    assert out.count("hash: ") == 2


def test_show_tail_larger_than_chain_shows_all(tmp_path, capsys):
    log = _build_chain(tmp_path, 2)
    rc = main(["show", "--log", str(log), "--tail", "99"])
    out = capsys.readouterr().out
    assert rc == EXIT_OK
    assert "seq=1" in out and "seq=2" in out


def test_show_tail_zero_and_negative_are_usage_errors(tmp_path, capsys):
    log = _build_chain(tmp_path, 1)
    for tail in ("0", "-1"):
        rc = main(["show", "--log", str(log), "--tail", tail])
        err = capsys.readouterr().err
        assert rc == EXIT_USAGE, tail
        assert "--tail must be >= 1" in err


# --- corrupt log ------------------------------------------------------------


def test_show_corrupt_log_is_operation_failure(tmp_path, capsys):
    log = tmp_path / "chain.jsonl"
    log.write_text("garbage\n", encoding="utf-8")
    rc = main(["show", "--log", str(log)])
    err = capsys.readouterr().err
    assert rc == EXIT_FAIL
    assert "not a valid record" in err


def test_show_corrupt_last_record_is_operation_failure(tmp_path, capsys):
    log = _build_chain(tmp_path, 2)
    lines = log.read_text(encoding="utf-8").splitlines()
    # Valid genesis, then a schema-invalid second line.
    log.write_text(lines[0] + "\n" + '{"seq": 2}\n', encoding="utf-8")
    rc = main(["show", "--log", str(log)])
    assert rc == EXIT_FAIL
    assert "line 2" in capsys.readouterr().err
