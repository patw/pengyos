"""Tests for the concurrency subtask.

The acceptance criterion: *concurrent appends (threads) produce a valid chain
of the expected length.*

Without a lock, ``append_event``'s read-last → build → atomic-rename sequence is
a lost-update race: two writers read the SAME head, both compute the SAME
``seq``/``prev_hash``, and one rename clobbers the other (the resulting chain
is shorter than the number of appends, or broken). Item 6 fixes this by holding
an exclusive ``fcntl.flock`` on a sibling ``<log>.lock`` file across the whole
critical section, serializing concurrent appenders.

Covers:

* the lock-file naming contract (:func:`chain.lock_path` / ``LOCK_SUFFIX``),
* mutual exclusion — an append BLOCKS while the lock is held and proceeds once
  it is released,
* no lock leak — an append that raises (corrupt log) still releases the lock,
* a high-contention threaded append race: N threads × K appends yield exactly
  N·K records forming a valid, link-consistent chain (verify passes),
* the same guarantee through the ``append`` CLI entry point.

These tests need POSIX ``fcntl``; they are skipped (not failed) elsewhere.
"""

import fcntl
import os
import threading

import pytest

from pengyos_attest import chain
from pengyos_attest.cli import EXIT_OK, main
from pengyos_attest.record import GENESIS_PREV_HASH, validate_record

POSIX = fcntl is not None  # guarded via a module-level check, not a runtime attr
pytestmark = pytest.mark.skipif(not POSIX, reason="requires POSIX fcntl")


# --- lock-file naming contract ---------------------------------------------


def test_lock_path_is_sibling_in_same_directory(tmp_path):
    log = tmp_path / "nested" / "chain.jsonl"
    lp = chain.lock_path(log)
    assert lp == log.parent / "chain.jsonl.lock"
    assert lp.parent == log.parent  # same directory (same filesystem)
    assert chain.LOCK_SUFFIX == ".lock"


# --- mutual exclusion -------------------------------------------------------


def test_append_blocks_while_lock_held_then_proceeds(tmp_path):
    """A second party holding the lock must delay an append; releasing lets it
    run. This proves the append actually takes the exclusive lock (it is not a
    no-op) — the core of the fix."""
    log = tmp_path / "chain.jsonl"
    lfp = chain.lock_path(log)
    lfp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lfp), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # we hold the head lock
        done = threading.Event()
        errors: "list[BaseException]" = []

        def worker() -> None:
            try:
                chain.append_event(log, "event", None, ts="2026-01-01T00:00:00Z")
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                done.set()

        t = threading.Thread(target=worker)
        t.start()
        # While we hold the lock, the append MUST block (not complete).
        assert not done.wait(0.5), "append should block while the lock is held"
        assert chain.chain_length(log) == 0  # nothing committed yet
        fcntl.flock(fd, fcntl.LOCK_UN)  # release -> the append can proceed
        t.join(timeout=5)
    finally:
        os.close(fd)
    assert not t.is_alive(), "append thread did not finish after release"
    assert not errors
    assert chain.chain_length(log) == 1


def test_no_lock_leak_after_failed_append(tmp_path):
    """If the append raises (corrupt log) inside the critical section, the lock
    must be released, so a later non-blocking acquire succeeds."""
    log = tmp_path / "c.jsonl"
    log.write_text("garbage\n", encoding="utf-8")
    with pytest.raises(chain.LogError):
        chain.append_event(log, "event", None)
    # Non-blocking acquire must succeed (would raise BlockingIOError if the
    # failed append had leaked the lock).
    fd = os.open(str(chain.lock_path(log)), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)


# --- high-contention race (the acceptance criterion) ------------------------


def _assert_valid_chain(log, expected: int) -> None:
    assert chain.chain_length(log) == expected
    report = chain.verify_chain(log)
    assert report.ok, report.reason
    prev = GENESIS_PREV_HASH
    for i, rec in enumerate(chain.read_records(log), start=1):
        validate_record(rec, expected_seq=i, expected_prev_hash=prev)
        prev = rec["hash"]


def test_concurrent_thread_appends_produce_full_valid_chain(tmp_path):
    """8 threads × 4 appends, all racing from a common barrier, must yield
    exactly 32 records in a valid, link-consistent chain. Without the lock this
    loses updates (length < 32) or breaks linkage."""
    log = tmp_path / "chain.jsonl"
    n_threads, per_thread = 8, 4
    barrier = threading.Barrier(n_threads)
    errors: "list[BaseException]" = []

    def worker() -> None:
        barrier.wait()
        for _ in range(per_thread):
            try:
                chain.append_event(log, "event", None)
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "a worker thread hung"
    assert not errors, errors
    _assert_valid_chain(log, n_threads * per_thread)


def test_concurrent_cli_appends_produce_full_valid_chain(tmp_path, capsys):
    """Same guarantee through the documented CLI entry point (each thread runs
    ``main(["append", ...])``)."""
    log = tmp_path / "chain.jsonl"
    n_threads, per_thread = 6, 3
    barrier = threading.Barrier(n_threads)
    codes: "list[int]" = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        for _ in range(per_thread):
            rc = main(["append", "--log", str(log), "--type", "event", "--data", "null"])
            with lock:
                codes.append(rc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()
    capsys.readouterr()  # drain concurrent stdout
    assert all(rc == EXIT_OK for rc in codes)
    _assert_valid_chain(log, n_threads * per_thread)


def test_concurrent_appends_are_deterministically_serialized(tmp_path):
    """Every record's prev_hash must equal the hash of the record just before
    it on disk — i.e. the head was re-read under the lock. This holds
    regardless of thread interleaving."""
    log = tmp_path / "chain.jsonl"
    n_threads = 10
    barrier = threading.Barrier(n_threads)

    def worker() -> None:
        barrier.wait()
        for _ in range(2):
            chain.append_event(log, "build", {"i": 1})

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    # Full length AND contiguous linkage (verify would fail on any clobber).
    _assert_valid_chain(log, n_threads * 2)
