"""Cross-PROCESS lock contention for ``sign``.

The earlier concurrency subtask (item 6) proved that ``append_event``
serializes concurrent writers — but only in-process (threads), and append
only. ``sign_head`` (item 7) has its own critical section: read the head,
check the sidecar for a duplicate ``(seq, head_hash, key)``, then
read-modify-rename the ``<log>.sigs`` sidecar. Two signers racing from
SEPARATE PROCESSES without a shared exclusive lock would both see "not yet
signed", both pass the duplicate refusal, and both write the sidecar —
a lost update or a duplicated entry, with BOTH reporting success.

``sign_head`` runs its whole section under the same ``fcntl.flock`` on the
sibling ``<log>.lock`` sidecar as ``append_event``. ``flock`` is per-inode
and shared across processes, so these tests are the ones that can actually
see the lock working *across process boundaries* (threads share the process,
so an in-process lock would pass their tests even if it were only
process-local).

Covers, all with REAL separate processes (no threads standing in):

* N barrier-synced worker processes racing ``chain.sign_head`` on the SAME
  head with the SAME key: EXACTLY ONE succeeds; every other process is
  refused by the duplicate check (``SigningError`` "already signed"); the
  sidecar ends up with exactly ONE valid, self-consistent entry covering
  the head; the chain itself is untouched; ``verify --public-key`` passes.
* The same race through the CLI entry point (subprocesses running
  ``pengyos-attest sign`` behind a start-gate file so they all release at
  the same instant): exactly one exits 0, the rest exit 1 with
  "already signed".
* A sign in a child PROCESS provably BLOCKS while another process holds the
  exclusive lock (the sidecar is not written, the child has not exited),
  and proceeds to success once the lock is released — direct evidence that
  the lock is a real cross-process exclusive lock, not a no-op.
* Cross-process interleaving: while parent-process signers contend, an
  ``append`` in a child process is also serialized against them — the
  append never observes a torn sidecar or a torn chain.

These tests need POSIX ``fcntl`` and process creation; they are skipped
(not failed) elsewhere.
"""

import json
import multiprocessing
import os
import subprocess
import sys
import threading

import pytest

from pengyos_attest import chain, ed25519, signing
from pengyos_attest.cli import EXIT_OK, EXIT_FAIL, main
from pengyos_attest.record import validate_record

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    fcntl is None, reason="requires POSIX fcntl")

# Tool root (parent of tests/): put it on a child subprocess' PYTHONPATH so
# the CLI child does not need the package to be pip-installed.
TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A fixed 32-byte seed; deterministic signatures keep every assertion
# reproducible. (Whether this seed is itself on the curve is irrelevant:
# signature entries carry the DERIVED public key, and `entry_is_valid`
# checks the signature under that key.)
SEED = bytes(range(32))
SEED_HEX = SEED.hex()
PUB = ed25519.publickey(SEED)


def _seed_file(tmp_path):
    """Write the seed as a 32 raw-byte key file and return its path."""
    key = tmp_path / "key"
    key.write_bytes(SEED)
    return str(key)


def _make_head(tmp_path, n: int = 2) -> "tuple[str, dict]":
    """Build a small chain and return ``(log_path, head_record)``."""
    log = tmp_path / "chain.jsonl"
    head = None
    for i in range(n):
        head = chain.append_event(
            log, "event", {"i": i}, ts=f"2026-01-01T00:00:{i:02d}Z")
    return str(log), head


def _assert_one_valid_signature(log_path, head, n_workers):
    """The shared post-race invariant: EXACTLY ONE signature in the sidecar,
    valid, covering the head; the chain untouched and verifying under the
    key."""
    sigs = signing.load_signatures(signing.sigs_path(log_path))
    assert len(sigs) == 1, (
        f"expected exactly 1 sidecar entry after {n_workers} concurrent "
        f"signers, got {len(sigs)}: {sigs}")
    entry = sigs[0]
    assert signing.entry_is_valid(entry), (
        f"the surviving sidecar entry is not a valid signature: {entry}")
    assert entry["seq"] == head["seq"]
    assert entry["head_hash"] == head["hash"]
    assert entry["public_key"] == PUB.hex()
    # Deterministic ed25519: the entry must be THE signature of the head
    # under the seed, whatever process produced it.
    assert entry["sig"] == ed25519.sign(
        signing.head_message(head["seq"], head["hash"]), SEED).hex()
    # The chain itself must be untouched and must verify under the key.
    assert chain.chain_length(log_path) == head["seq"]
    report = chain.verify_chain(log_path, public_key=PUB)
    assert report.ok, report.reason
    assert report.signature_count == 1


# --- multiprocessing race: library level ------------------------------------


def _sign_worker(log_path: str, barrier: multiprocessing.synchronization.Barrier,
                 results: "multiprocessing.Queue") -> None:
    """Child process: hit the barrier (release all signers at once), then
    attempt exactly ONE ``sign_head`` and report the outcome."""
    try:
        barrier.wait(timeout=30)
    except Exception as exc:  # pragma: no cover - surfaced via results
        results.put(("error", repr(exc)))
        return
    try:
        chain.sign_head(log_path, SEED)
        results.put(("ok", None))
    except signing.SigningError as exc:
        results.put(("dup", str(exc)))
    except Exception as exc:  # pragma: no cover - surfaced via results
        results.put(("error", repr(exc)))


def test_concurrent_cross_process_signs_exactly_one_wins(tmp_path):
    """6 worker PROCESSES, barrier-synchronized, all signing the SAME head
    with the SAME key: exactly one must succeed and the other five must be
    refused by the duplicate check. Without the cross-process lock, two or
    more would pass the duplicate check simultaneously (both read an empty
    sidecar) and both would 'succeed' — which this test catches."""
    log, head = _make_head(tmp_path)
    ctx = multiprocessing.get_context("fork")  # Linux target; inherit sys.path
    barrier = ctx.Barrier(6)
    results: "multiprocessing.Queue" = ctx.Queue()

    procs = [ctx.Process(target=_sign_worker, args=(log, barrier, results))
             for _ in range(6)]
    for p in procs:
        p.start()
    outcomes = [results.get(timeout=60) for _ in range(6)]
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, (
            f"sign worker died (exit {p.exitcode}): {outcomes}")
    oks = [o for o in outcomes if o[0] == "ok"]
    dups = [o for o in outcomes if o[0] == "dup"]
    errors = [o for o in outcomes if o[0] == "error"]
    assert not errors, errors
    assert len(oks) == 1, (
        f"exactly one signer may succeed, got {len(oks)} "
        f"(lost update / duplicate check race): {outcomes}")
    assert len(dups) == 5
    for _, msg in dups:
        assert "already signed" in msg, msg
    _assert_one_valid_signature(log, head, 6)


# --- multiprocessing race: CLI (subprocess) level ---------------------------

# The child: print READY (so the parent knows all children are parked), wait
# for the start-gate file, then run the real CLI entry point in-process.
_CHILD_SIGN_SCRIPT = r"""
import os, sys, time
sys.path.insert(0, os.environ["PENGYOS_TEST_ROOT"])
print("READY", flush=True)
while not os.path.exists(os.environ["PENGYOS_TEST_GATE"]):
    time.sleep(0.005)
from pengyos_attest.cli import main
sys.exit(main(["sign", "--log", os.environ["PENGYOS_TEST_LOG"],
               "--signing-key", os.environ["PENGYOS_TEST_KEY"]]))
"""


def test_concurrent_cross_process_cli_signs_exactly_one_wins(tmp_path):
    """Same contract through the CLI: N subprocesses run
    ``pengyos-attest sign`` simultaneously (released together by a start-gate
    file). Exactly one exits 0; the rest exit 1 and report
    'already signed' on stderr."""
    log, head = _make_head(tmp_path)
    key = _seed_file(tmp_path)
    gate = tmp_path / "go"
    env = dict(os.environ)
    env["PYTHONPATH"] = TOOL_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.update({
        "PENGYOS_TEST_ROOT": TOOL_ROOT,
        "PENGYOS_TEST_GATE": str(gate),
        "PENGYOS_TEST_LOG": log,
        "PENGYOS_TEST_KEY": key,
    })
    n = 5
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD_SIGN_SCRIPT],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=TOOL_ROOT,
        )
        for _ in range(n)
    ]
    try:
        # Each child prints exactly one READY before it parks on the gate;
        # drain them one per child (each pipe is independent, so this cannot
        # hang), THEN release every child at the same instant.
        for p in procs:
            line = p.stdout.readline().strip()
            assert line == "READY", f"child said {line!r}"
        gate.write_text("go\n")
        outcomes = []
        for p in procs:
            p.wait(timeout=60)
            outcomes.append((p.returncode, p.stderr.read()))
    finally:
        for p in procs:
            if p.poll() is None:  # pragma: no cover - defensive
                p.kill()
    ok = [rc for rc, err in outcomes if rc == EXIT_OK]
    fail = [(rc, err) for rc, err in outcomes if rc != EXIT_OK]
    assert len(ok) == 1, (
        f"exactly one CLI signer may exit 0, got {len(ok)}: {outcomes}")
    assert len(fail) == n - 1
    for rc, err in fail:
        assert rc == EXIT_FAIL, f"expected exit {EXIT_FAIL}, got {rc}: {err}"
        assert "already signed" in err, err
    _assert_one_valid_signature(log, head, n)


# --- cross-process block: the lock is real across process boundaries --------


# Like _CHILD_SIGN_SCRIPT, but the parent reads READY and THEN acquires the
# lock BEFORE opening the gate: the lock is therefore guaranteed held by the
# parent before the child can possibly reach the sign call (no first-come
# race), and everything after READY is the child trying to sign.
_CHILD_GATED_SIGN_SCRIPT = r"""
import os, sys, time
sys.path.insert(0, os.environ["PENGYOS_TEST_ROOT"])
print("READY", flush=True)
while not os.path.exists(os.environ["PENGYOS_TEST_GATE"]):
    time.sleep(0.005)
from pengyos_attest.cli import main
sys.exit(main(["sign", "--log", os.environ["PENGYOS_TEST_LOG"],
               "--signing-key", os.environ["PENGYOS_TEST_KEY"]]))
"""


def test_child_process_sign_blocks_while_other_process_holds_lock(tmp_path):
    """Direct proof the lock is cross-process: the parent process holds the
    exclusive ``flock`` on ``<log>.lock`` (acquired BEFORE the gated child
    is released, so no first-come race); the child PROCESS attempting
    ``sign`` must block — the sidecar is NOT written and the child has not
    exited — and must succeed only after the parent releases the lock."""
    log, head = _make_head(tmp_path)
    key = _seed_file(tmp_path)
    sigs = signing.sigs_path(log)
    assert not sigs.exists()

    lfp = chain.lock_path(log)
    fd = os.open(str(lfp), os.O_CREAT | os.O_RDWR, 0o644)
    gate = tmp_path / "go"
    env = dict(os.environ)
    env["PYTHONPATH"] = TOOL_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env.update({
        "PENGYOS_TEST_ROOT": TOOL_ROOT,
        "PENGYOS_TEST_GATE": str(gate),
        "PENGYOS_TEST_LOG": log,
        "PENGYOS_TEST_KEY": key,
    })
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD_GATED_SIGN_SCRIPT],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=TOOL_ROOT,
    )
    try:
        assert child.stdout.readline().strip() == "READY"
        fcntl.flock(fd, fcntl.LOCK_EX)  # WE hold the head lock — NOW
        # Only after the lock is held do we release the child to sign.
        gate.write_text("go\n")
        # The child MUST stay parked on the lock; it cannot write or exit.
        assert not _exited_within(child, 0.7), (
            "sign child completed while another process held the lock — "
            "the lock is not exclusive across processes")
        assert not sigs.exists(), "sidecar written while the lock was held"
        fcntl.flock(fd, fcntl.LOCK_UN)  # release -> the child can proceed
        rc = child.wait(timeout=30)
    finally:
        os.close(fd)
        child.kill()  # harmless if already exited
    assert rc == EXIT_OK, child.stderr.read()
    _assert_one_valid_signature(log, head, 1)


def _exited_within(proc: subprocess.Popen, seconds: float) -> bool:
    """True if *proc* exits within *seconds* (0.1 s resolution)."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


# --- mixed cross-process append vs sign: one lock, one head ------------------


def _append_worker(log_path: str, barrier, results) -> None:
    """Child process: barrier, then append ONE record."""
    try:
        barrier.wait(timeout=30)
        chain.append_event(log_path, "build", {"worker": True})
        results.put(("ok", None))
    except Exception as exc:  # pragma: no cover - surfaced via results
        results.put(("error", repr(exc)))


def test_cross_process_append_and_sign_share_the_lock(tmp_path):
    """Appenders (child processes) and signers (parent thread) all race from
    one barrier. The shared ``<log>.lock`` must serialize them so that: the
    chain is a valid, complete, link-consistent chain; no append was lost;
    and every sidecar entry is valid — a signer that got in between two
    appends anchored the head it actually saw."""
    log, head = _make_head(tmp_path)
    n_appends, n_signers = 4, 4
    total = n_appends + n_signers
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(total)
    results: "multiprocessing.Queue" = ctx.Queue()

    procs = [ctx.Process(target=_append_worker, args=(log, barrier, results))
             for _ in range(n_appends)]
    threads = [threading.Thread(target=_signer_thread, args=(log, barrier))
               for _ in range(n_signers)]
    for p in procs:
        p.start()
    for t in threads:
        t.start()
    outcomes = [results.get(timeout=60) for _ in range(n_appends)]
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, outcomes
    for t in threads:
        t.join(timeout=60)
    assert all(o[0] == "ok" for o in outcomes), outcomes

    final_len = head["seq"] + n_appends
    assert chain.chain_length(log) == final_len
    report = chain.verify_chain(log)
    assert report.ok, report.reason
    # Every sidecar entry must be a valid signature over SOME record that
    # really was in the chain (seq within 1..final_len, matching hash).
    records = chain.read_records(log)
    by_seq = {r["seq"]: r["hash"] for r in records}
    entries = signing.load_signatures(signing.sigs_path(log))
    assert len(entries) >= 1
    for entry in entries:
        assert signing.entry_is_valid(entry), entry
        assert entry["seq"] in by_seq, entry
        assert entry["head_hash"] == by_seq[entry["seq"]], entry
    # And linkage is contiguous end to end.
    from pengyos_attest.record import GENESIS_PREV_HASH
    prev = GENESIS_PREV_HASH
    for i, rec in enumerate(records, start=1):
        validate_record(rec, expected_seq=i, expected_prev_hash=prev)
        prev = rec["hash"]


def _signer_thread(log_path: str, barrier) -> None:
    """Parent thread: barrier, then one ``sign_head`` (errors ignored here —
    duplicate refusal is an expected outcome of this mixed race; the shared
    invariants are asserted by the test)."""
    try:
        barrier.wait(timeout=30)
    except Exception:
        return
    try:
        chain.sign_head(log_path, SEED)
    except signing.SigningError:
        pass  # already signed by an earlier signer: fine
