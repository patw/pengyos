"""Append-only JSONL hash-chain file I/O (append, item 3; verify, item 4;
export, item 5).

A log file is a sequence of records, one :func:`.record.to_json_line` per line
(``newline`` separated), in contiguous ``seq`` order. :func:`append_event`
implements the atomic, crash-safe append::

    read the last record  ->  build the next record (prev_hash = last.hash,
                              seq = last.seq + 1)  ->  rewrite the log via a
    temp file + fsync + atomic rename (os.replace).

Why a temp-file + rename (rather than a bare ``open(..., "a")`` write)?

* The rename makes the on-disk file switch from "old content" to "old content
  + new line" in one atomic step: a crash or a reader that races the write
  never observes a half-written line (the acceptance criterion "appends are
  atomic and crash-safe").
* The fsync of the temp file (and of its directory) forces the new bytes to
  durable storage *before* the rename, so a power loss cannot leave the chain
  pointing at a file whose final line is absent or torn.

Concurrency (item 6): ``append_event`` holds an exclusive advisory lock (POSIX
``fcntl.flock``) on a sibling ``<log>.lock`` file across the whole
read-last-record → build → atomic-rename critical section, so two appends —
even from separate processes or threads — are serialized and neither can read a
stale head or clobber the other's write (the last-rename-wins / lost-update race
item 3 was explicit about not being safe against). The ``.lock`` file is a
stable inode that is never renamed, so the lock is not disturbed by the log's
own ``os.replace``. The lock file is an expected, permanent sibling of the log
(not a temp-file stray) and is not part of the chain.

``verify_chain`` (item 4) walks a log line by line and reports — instead of
raising — the first place the chain breaks. A non-blank line must be a record
whose schema and self-hash are valid, whose ``seq`` is exactly one more than
the previous record's, and whose ``prev_hash`` equals the previous record's
``hash`` (the genesis record links to ``"0" * 64``). The first failing record
yields the report's ``first_bad_seq`` (or ``first_bad_line`` when the line is
not even a parseable record); the walk stops there. An optional ``anchor``
(the ``hash`` of the head record an external observer previously committed to)
detects tail truncation: a chain that is otherwise valid but ends before the
anchor is reported as truncated, because a plain self-consistent prefix gives
no hint that records were lost.

``export_chain`` (item 5) bundles the whole chain with a Merkle-ish root
hash: a Merkle tree whose leaves are the records' ``hash`` values, with
adjacent nodes paired by ``sha256(ascii(left + right))`` and an odd final
node duplicated before pairing (Bitcoin style); the empty chain has the
fixed root ``sha256(b"")``. The bundle is a self-contained JSON document
(see :func:`export_chain`) that :func:`verify_export` can re-check without
touching the original log.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence, Tuple

try:  # POSIX advisory file locking — the platform this tool targets (Linux).
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - non-POSIX fallback (see _file_lock)
    fcntl = None  # type: ignore[assignment]

from . import ed25519, signing
from .record import (
    GENESIS_PREV_HASH,
    RecordError,
    from_json_line,
    new_record,
    to_json_line,
    utc_now,
    validate_record,
)

__all__ = [
    "LogError",
    "VerifyReport",
    "chain_length",
    "last_record",
    "read_records",
    "append_event",
    "verify_chain",
    "sign_head",
    "merkle_root",
    "export_chain",
    "verify_export",
    "lock_path",
    "sigs_path",
    "EXPORT_FORMAT",
    "EXPORT_FORMAT_VERSION",
    "SUPPORTED_EXPORT_FORMAT_VERSIONS",
    "EMPTY_CHAIN_ROOT",
    "LOCK_SUFFIX",
    "SIGS_SUFFIX",
]


class LogError(Exception):
    """Raised when an append cannot be performed on a log file."""


#: Suffix of the signature sidecar (item 7; see :mod:`.signing`).
SIGS_SUFFIX: str = signing.SIGS_SUFFIX


def sigs_path(log_path: "os.PathLike[str] | str") -> Path:
    """The head-signature sidecar path for a log: ``<log>.sigs`` (item 7)."""
    return signing.sigs_path(log_path)


#: Suffix appended to a log's filename to name its exclusive-lock sidecar file.
#: The lock file is a STABLE inode that is never renamed, so the advisory lock
#: survives the log's own atomic ``os.replace`` (renaming the log would drop any
#: lock held on the old inode). It is not part of the chain.
LOCK_SUFFIX: str = ".lock"


def lock_path(log_path: "os.PathLike[str] | str") -> Path:
    """Return the path of the exclusive-lock sidecar file for a log (item 6).

    For a log at ``<dir>/<name>`` this is ``<dir>/<name>.lock`` — a sibling in
    the SAME directory (advisory ``flock`` locks are per-inode, and a sibling in
    the same directory guarantees the lock and the log are on the same
    filesystem, which is what makes the lock meaningful across the rename).
    """
    path = Path(log_path)
    return path.with_name(path.name + LOCK_SUFFIX)


@contextlib.contextmanager
def _file_lock(log_path: "os.PathLike[str] | str") -> Iterator[None]:
    """Hold an exclusive advisory lock (item 6) for the duration of the block.

    The lock is a ``fcntl.flock`` on the ``<log>.lock`` sidecar file (see
    :func:`lock_path`). ``flock`` is advisory and per-inode, is inherited by
    ``fork()``-ed children, and is released automatically when the descriptor
    is closed (including on crash), so a dead writer cannot wedge the chain.
    On non-POSIX platforms without ``fcntl`` this degrades to a no-op (single
    writer); on POSIX it blocks until the lock is free, serializing concurrent
    appenders.
    """
    if fcntl is None:  # pragma: no cover - POSIX-only; guarded by tests
        yield
        return
    path = Path(log_path)
    lfp = lock_path(path)
    lfp.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lfp), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_records(log_path: "os.PathLike[str] | str") -> "list[dict]":
    """Read and validate every record in the log, in file order.

    Blank lines are ignored. Any non-blank line that is not a valid record
    raises :class:`LogError` (wrapping the :class:`.record.RecordError`).
    An empty or missing file yields an empty list.
    """
    path = Path(log_path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    records = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(from_json_line(line))
        except Exception as exc:  # RecordError or unexpected
            raise LogError(f"line {lineno} is not a valid record: {exc}") from exc
    return records


def chain_length(log_path: "os.PathLike[str] | str") -> int:
    """The number of records in the log (0 for a missing/empty file)."""
    return len(read_records(log_path))


def last_record(log_path: "os.PathLike[str] | str") -> Optional[dict]:
    """The final record in the log, or ``None`` if the log is empty/missing."""
    records = read_records(log_path)
    return records[-1] if records else None


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically (temp file + fsync + rename)."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Never leave a stray temp file behind on any failure.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    # Best-effort durability of the directory entry (the rename itself).
    _fsync_dir(directory)


def _fsync_dir(directory: Path) -> None:
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def append_event(
    log_path: "os.PathLike[str] | str",
    event_type: str,
    data: Any = None,
    *,
    ts: Optional[str] = None,
) -> dict:
    """Append one event record to the log and return the new record.

    Reads the last record to derive the next ``prev_hash`` and ``seq`` (a
    missing/empty log starts the chain at the genesis record), builds the next
    self-consistent record via :func:`.record.new_record`, and writes it
    atomically (see module docstring). Raises :class:`LogError` on a corrupt
    existing log, and :class:`.record.RecordError` for invalid ``event_type`` /
    ``data`` / ``ts``.

    The whole read-last-record → build → atomic-rename sequence runs under an
    exclusive file lock (item 6, :func:`_file_lock`) so that concurrent
    appenders — from other threads or processes — are serialized: each one sees
    the other's committed head before computing its own ``seq`` /
    ``prev_hash``, so no two appends write the same ``seq`` or clobber each
    other's write.
    """
    path = Path(log_path)
    # Resolve the timestamp before the critical section; the record body
    # (type/data/ts) is fixed per-append, so only the head-derived fields
    # (seq, prev_hash) and the on-disk rewrite belong inside the lock.
    record_body = (ts if ts is not None else utc_now())
    with _file_lock(path):
        last = last_record(path)
        if last is None:
            seq, prev_hash = 1, GENESIS_PREV_HASH
        else:
            seq, prev_hash = last["seq"] + 1, last["hash"]
        record = new_record(seq, record_body, event_type, data,
                            prev_hash=prev_hash)

        existing = ""
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
        content = existing + to_json_line(record) + "\n"
        _atomic_write_text(path, content)
    return record


@dataclass
class VerifyReport:
    """Structured outcome of :func:`verify_chain`.

    Attributes:
        ok: ``True`` when the chain passed every check that was run.
        record_count: number of non-blank lines found in the file (0 for a
            missing or empty log).
        first_bad_seq: the ``seq`` of the first record that failed a check,
            or ``None`` when every record passed (or the failing line is not a
            parseable record, in which case ``first_bad_line`` is set).
        first_bad_line: the 1-based file line number of the first failing
            non-blank line, or ``None`` when every line passed.
        reason: a human-readable explanation of the failure; ``None`` when
            ``ok`` is true.
        signature_count: when the signature sidecar was consulted (it exists,
            or ``public_key`` was requested), the number of entries it held;
            ``None`` when no signature check was performed.
    """

    ok: bool
    record_count: int
    first_bad_seq: Optional[int] = None
    first_bad_line: Optional[int] = None
    reason: Optional[str] = None
    signature_count: Optional[int] = None


def verify_chain(
    log_path: "os.PathLike[str] | str",
    *,
    anchor: Optional[str] = None,
    public_key: Optional["bytes | list"] = None,
) -> VerifyReport:
    """Verify the hash chain at ``log_path`` and return a :class:`VerifyReport`.

    Never raises for corrupt content: every finding is returned in the
    report. Only I/O problems (``OSError``) propagate.

    Checks, for each non-blank line in file order:

    1. the line parses as a JSON object and satisfies the record schema and
       its self-hash rule (recomputed, so any field edit is caught);
    2. ``seq`` is contiguous starting at 1 (reordering, duplication, and
       gaps all fail here);
    3. ``prev_hash`` links to the previous record's ``hash`` (the first
       record must link to the genesis value).

    The walk stops at the first failing record: its ``seq`` (or the line
    number, when the line is not a parseable record) is reported.

    ``anchor`` (optional): the ``hash`` of a previously committed head record.
    When given, the final record must have exactly that hash — otherwise the
    chain is reported as truncated (records lost after the prefix currently on
    disk). Without an anchor, truncation of a self-consistent prefix is
    undetectable and verify reports it as OK; the anchor is what makes the
    lost tail provable.

    ``public_key`` (optional, item 7): a 32-byte ed25519 public key, or a
    list of candidate public keys (see
    :func:`.signing.verification_key_candidates`), that must have signed the
    chain head. When given, the ``<log>.sigs`` sidecar must contain a VALID
    signature entry covering the final record — same ``seq``, same
    ``head_hash``, and an entry ``public_key`` among the candidates.
    Whenever the ``<log>.sigs`` sidecar exists, EVERY entry in it is checked
    for self-consistency (a tampered entry fails verify even without
    ``public_key``). A chain that was once signed and then truncated away
    from its signed head therefore fails: the sidecar proves the lost tail
    even without ``--anchor``.

    Check order: the chain walk (and any anchor mismatch) is reported BEFORE
    a signature finding, so tamper wins over a missing anchor signature.
    """
    path = Path(log_path)
    if not path.exists():
        if public_key is not None:
            return VerifyReport(
                ok=False, record_count=0, signature_count=0,
                reason=("cannot verify a head signature for an empty chain "
                        "(no head record to sign)"),
            )
        return VerifyReport(ok=True, record_count=0)
    text = path.read_text(encoding="utf-8")
    report, records = _walk_records(text.splitlines())
    if not report.ok:
        return report
    if anchor is not None and not records:
        return VerifyReport(
            ok=False, record_count=0,
            reason=(f"truncated: anchored head hash {anchor[:12]}... is not "
                    "present in an empty chain"),
        )
    if anchor is not None and records[-1]["hash"] != anchor:
        return VerifyReport(
            ok=False, record_count=len(records),
            reason=(f"truncated: chain ends at seq {records[-1]['seq']} "
                    f"(hash {records[-1]['hash'][:12]}...) but the anchored "
                    f"head is {anchor[:12]}... — records after seq "
                    f"{records[-1]['seq']} were lost"),
        )
    sig_error, sig_count = _check_signatures(path, records, public_key)
    if sig_error is not None:
        return VerifyReport(
            ok=False, record_count=len(records), reason=sig_error,
            signature_count=sig_count,
        )
    report.signature_count = sig_count
    return report


def _check_signatures(
    path: Path,
    records: list,
    public_key: Optional["bytes | list"],
) -> "tuple[Optional[str], Optional[int]]":
    """Signature-sidecar checks for :func:`verify_chain` (item 7).

    Returns ``(error, signature_count)``. ``error`` is ``None`` when every
    check passed (or no check was due); ``signature_count`` is the number of
    entries in the sidecar when the sidecar was consulted, else ``None``.

    The sidecar is consulted when it EXISTS (every entry must be a valid
    signature over its own ``seq``/``head_hash`` under its own
    ``public_key``) or when *public_key* was requested (the current head must
    be covered by a valid entry from that key).
    """
    sigs = signing.sigs_path(path)
    if not sigs.exists() and public_key is None:
        return None, None
    try:
        entries = signing.load_signatures(sigs)
    except signing.SigningError as exc:
        return f"signature log {sigs.name}: {exc}", None
    count = len(entries)
    for lineno, entry in enumerate(entries, start=1):
        if not signing.entry_is_valid(entry):
            return (
                f"signature log {sigs.name} line {lineno}: signature invalid "
                f"for seq={entry.get('seq')} (head "
                f"{str(entry.get('head_hash'))[:12]}...) — the signature does "
                "not match its own seq/hash/key",
                count,
            )
    if public_key is not None:
        candidates = (
            public_key if isinstance(public_key, (list, tuple)) else [public_key]
        )
        if not records:
            return (
                "cannot verify a head signature for an empty chain "
                "(no head record to sign)",
                count,
            )
        head = records[-1]
        covered = any(
            entry["seq"] == head["seq"]
            and entry["head_hash"] == head["hash"]
            and bytes.fromhex(entry["public_key"]) in candidates
            for entry in entries
        )
        if not covered:
            label = candidates[0].hex()[:12]
            where = ("no signature in " + sigs.name if not entries
                     else f"no covering signature in {sigs.name}")
            return (
                f"head seq {head['seq']} (hash {head['hash'][:12]}...) has "
                f"{where} from key {label}... — the chain head is not anchored "
                "by that key",
                count,
            )
    return None, count


def sign_head(
    log_path: "os.PathLike[str] | str",
    seed: bytes,
    *,
    ts: Optional[str] = None,
) -> dict:
    """Sign the current head record of the log (item 7) and return the entry.

    The chain is verified FIRST: a corrupt chain is refused
    (:class:`LogError`), so a broken chain cannot be anchored. The rest runs
    under the SAME exclusive lock as :func:`append_event`: the head is
    re-read inside the lock, the signature entry is built via
    :func:`.signing.new_signature_entry` (a self-verifying entry over
    :func:`.signing.head_message` — the head's ``seq`` and ``hash``), a head
    that is ALREADY signed by the same key is refused
    (:class:`.signing.SigningError`), and the entry is appended to the
    ``<log>.sigs`` sidecar via the same atomic write (temp + fsync + rename)
    as the log itself, so a crash never leaves a torn sidecar.

    Raises :class:`LogError` for a corrupt or empty chain, and
    :class:`.signing.SigningError` when the head is already signed by this
    key (or the sidecar itself is unreadable). A bad *seed* length raises
    ``ValueError``.
    """
    path = Path(log_path)
    report = verify_chain(path)
    if not report.ok:
        raise LogError(f"cannot sign a corrupt chain: {report.reason}")
    with _file_lock(path):
        last = last_record(path)
        if last is None:
            raise LogError("cannot sign an empty chain (no head record)")
        if len(seed) != ed25519.SEED_LEN:
            raise ValueError(
                f"ed25519 seed must be {ed25519.SEED_LEN} bytes, got {len(seed)}")
        pub_hex = ed25519.publickey(seed).hex()
        sigs = signing.sigs_path(path)
        entries = signing.load_signatures(sigs)
        if any(
            e["seq"] == last["seq"]
            and e["head_hash"] == last["hash"]
            and e.get("public_key") == pub_hex
            for e in entries
        ):
            raise signing.SigningError(
                f"head seq {last['seq']} is already signed by this key "
                f"(see {sigs.name}); a new signature requires a new head "
                "record or a different key")
        entry = signing.new_signature_entry(
            last["seq"], last["hash"], seed, ts=ts)
        existing = sigs.read_text(encoding="utf-8") if sigs.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True)
        _atomic_write_text(sigs, existing + line + "\n")
    return entry


def _walk_records(lines: "Sequence[str]") -> "Tuple[VerifyReport, list]":
    """Walk a sequence of file lines as chain records (see :func:`verify_chain`).

    Returns ``(report, records)`` where ``records`` holds the records that
    passed every check *before* the first failure (all records on success).
    On failure the report carries ``first_bad_seq`` / ``first_bad_line`` and a
    reason; the walk stops at the first failing line. Shared by
    :func:`verify_chain` (file lines) and :func:`verify_export` (the bundle's
    re-serialized records) so both enforce byte-identical chain rules.
    """
    records: "list[dict]" = []
    prev_hash: str = GENESIS_PREV_HASH
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            return VerifyReport(
                ok=False, record_count=len(records) + 1, first_bad_line=lineno,
                reason=f"line {lineno} is not valid JSON: {exc.msg}",
            ), records
        try:
            validate_record(raw)
        except RecordError as exc:
            seq = raw.get("seq") if isinstance(raw, dict) else None
            return VerifyReport(
                ok=False, record_count=len(records) + 1,
                first_bad_seq=(seq if isinstance(seq, int) else None),
                first_bad_line=lineno,
                reason=f"line {lineno}: {exc}",
            ), records
        expected_seq = len(records) + 1
        if raw["seq"] != expected_seq:
            return VerifyReport(
                ok=False, record_count=len(records) + 1,
                first_bad_seq=raw["seq"], first_bad_line=lineno,
                reason=(f"line {lineno}: seq {raw['seq']} where seq "
                        f"{expected_seq} is expected (reordered or duplicate "
                        "record)"),
            ), records
        if raw["prev_hash"] != prev_hash:
            return VerifyReport(
                ok=False, record_count=len(records) + 1,
                first_bad_seq=raw["seq"], first_bad_line=lineno,
                reason=(f"line {lineno}: prev_hash {raw['prev_hash'][:12]}... "
                        f"does not link to the previous record's hash "
                        f"{prev_hash[:12]}... (chain broken)"),
            ), records
        records.append(raw)
        prev_hash = raw["hash"]
    return VerifyReport(ok=True, record_count=len(records)), records


#: Export-bundle format identifier (checked by :func:`verify_export`).
EXPORT_FORMAT: str = "pengyos-attest/export"
#: Export-bundle format version produced by :func:`export_chain` (v2 adds the
#: head-signature sidecar snapshot; see the bundle format in its docstring).
EXPORT_FORMAT_VERSION: int = 2
#: Format versions :func:`verify_export` accepts. v1 (pre-signing) bundles
#: carry no ``sigs`` key; v2 bundles carry the signature sidecar snapshot.
SUPPORTED_EXPORT_FORMAT_VERSIONS: "tuple[int, ...]" = (1, 2)
#: Merkle root of an EMPTY chain: the fixed constant ``sha256(b"")``.
EMPTY_CHAIN_ROOT: str = hashlib.sha256(b"").hexdigest()


def merkle_root(hashes: "Sequence[str]") -> str:
    """Merkle-ish root over an ordered list of record ``hash`` values.

    The leaves are the record hashes in ``seq`` order. Each level pairs
    adjacent nodes: a parent is ``sha256(ascii(left + right)).hexdigest()``.
    An odd final node is DUPLICATED before pairing (Bitcoin style), so the
    tree never drops a leaf. An empty list yields the fixed
    :data:`EMPTY_CHAIN_ROOT` (``sha256(b"")``).

    The root therefore changes when ANY record is added, removed, or edited —
    it is a one-hash anchor for the whole chain, suitable for external
    attestation (e.g. committing the root out-of-band and re-checking it
    later with :func:`verify_export` / :func:`verify_chain --anchor`).
    """
    if not hashes:
        return EMPTY_CHAIN_ROOT
    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            hashlib.sha256((level[i] + level[i + 1]).encode("ascii")).hexdigest()
            for i in range(0, len(level), 2)
        ]
    return level[0]


def export_chain(
    log_path: "os.PathLike[str] | str",
    out_path: "os.PathLike[str] | str",
    *,
    ts: Optional[str] = None,
) -> dict:
    """Bundle the chain at ``log_path`` with a Merkle root into ``out_path``.

    The source chain is verified FIRST: exporting a corrupt chain raises
    :class:`LogError` (a broken chain must not be bundled into something an
    observer might trust). A missing/empty log exports a valid empty bundle.

    The bundle is a self-contained JSON document (``EXPORT_FORMAT``
    version 2)::

        {"format": "pengyos-attest/export", "format_version": 2,
         "exported_at": <ts>, "record_count": N,
         "head_hash": <final record hash, or null>,
         "root_hash": <Merkle root of the record hashes>,
         "records": [<all records, in seq order>],
         "sigs": [<every entry of the <log>.sigs sidecar, in file order>,
                  or [] when the sidecar does not exist]}

    The ``sigs`` key is the v2 addition: a snapshot of the head-signature
    sidecar so an observer holding ONLY the bundle (not the live ``.sigs``
    file) can re-check every signature (each entry is self-verifying — it
    carries its own ``public_key``) and can see whether the head is anchored.
    When the sidecar exists it was already re-validated by the pre-export
    :func:`verify_chain` call above, so a bundle never carries a corrupt or
    invalid entry. A missing/empty sidecar exports ``[]`` (signing is
    optional). It is written atomically (temp file + fsync + rename) like
    the log, so a crash never leaves a torn bundle. Returns the bundle dict.
    """
    path = Path(log_path)
    report = verify_chain(path)
    if not report.ok:
        raise LogError(f"cannot export a corrupt chain: {report.reason}")
    records = read_records(path)  # guaranteed clean by the verify above
    sigs = signing.load_signatures(signing.sigs_path(path))
    bundle = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": ts if ts is not None else utc_now(),
        "record_count": len(records),
        "head_hash": records[-1]["hash"] if records else None,
        "root_hash": merkle_root([r["hash"] for r in records]),
        "records": records,
        "sigs": sigs,
    }
    content = json.dumps(bundle, sort_keys=True, indent=2) + "\n"
    _atomic_write_text(Path(out_path), content)
    return bundle


def verify_export(bundle: Any) -> Optional[str]:
    """Verify an export bundle (see :func:`export_chain`).

    Returns ``None`` when the bundle is fully consistent, else a
    human-readable error string — it never raises for corrupt content.

    Both format versions are accepted (see
    :data:`SUPPORTED_EXPORT_FORMAT_VERSIONS`): version 1 bundles predate
    head signing and must NOT carry a ``sigs`` key; version 2 bundles carry
    ``sigs`` — a list of signature sidecar entries that is checked in
    addition to the v1 checks (see :func:`_check_bundle_sigs`).

    Checks, in order: the ``format`` / ``format_version`` identifiers;
    ``records`` is a list of JSON objects; the records form a valid chain
    under the SAME rules as :func:`verify_chain` (schema + self-hash +
    contiguous ``seq`` + ``prev_hash`` linkage, walked via the shared
    :func:`_walk_records`); ``record_count``, ``head_hash``, and
    ``root_hash`` (recomputed with :func:`merkle_root`) all agree with the
    records; and (v2 only) ``sigs`` is a list of well-formed signature
    entries (shape per :func:`.signing.entry_shape_error`) each carrying a
    VALID ed25519 signature over its own ``seq``/``head_hash`` under its
    own ``public_key``. Any mismatch — e.g. an edited record, a dropped
    tail, a stale ``root_hash``, or a tampered signature entry — is
    reported.
    """
    if not isinstance(bundle, dict):
        return "bundle must be a JSON object, got " + type(bundle).__name__
    if bundle.get("format") != EXPORT_FORMAT:
        return f"format must be {EXPORT_FORMAT!r}, got {bundle.get('format')!r}"
    version = bundle.get("format_version")
    if version not in SUPPORTED_EXPORT_FORMAT_VERSIONS:
        return (f"format_version must be one of "
                f"{SUPPORTED_EXPORT_FORMAT_VERSIONS}, got {version!r}")
    records = bundle.get("records")
    if not isinstance(records, list):
        return "'records' must be a list of record objects"
    lines = []
    for index, rec in enumerate(records):
        if not isinstance(rec, dict):
            return f"records[{index}] is not a JSON object"
        lines.append(to_json_line(rec))
    report, _ = _walk_records(lines)
    if not report.ok:
        return f"records do not form a valid chain: {report.reason}"
    if bundle.get("record_count") != len(records):
        return (f"record_count {bundle.get('record_count')!r} does not match "
                f"the {len(records)} records present")
    expected_head = records[-1]["hash"] if records else None
    if bundle.get("head_hash") != expected_head:
        return (f"head_hash {bundle.get('head_hash')!r} does not match the "
                f"final record's hash {expected_head!r}")
    expected_root = merkle_root([r["hash"] for r in records])
    if bundle.get("root_hash") != expected_root:
        return (f"root_hash {bundle.get('root_hash')!r} does not match the "
                f"recomputed Merkle root {expected_root!r}")
    return _check_bundle_sigs(bundle, version)


def _check_bundle_sigs(bundle: dict, version: int) -> Optional[str]:
    """Signature-section checks for :func:`verify_export` (format v2).

    v1 bundles must NOT carry a ``sigs`` key at all (the field did not
    exist then; tolerating one would let a v1 bundle smuggle in signatures
    that were never under the v2 contract). v2 bundles MUST carry
    ``sigs`` as a list; every entry must be a well-formed signature entry
    (shape per :func:`.signing.entry_shape_error`) and carry a VALID
    ed25519 signature over its own ``seq``/``head_hash`` under its own
    ``public_key`` (:func:`.signing.entry_is_valid`) — exactly the rule
    :func:`verify_chain` applies to the live sidecar, so a bundle proves
    the same signatures the observer would see on the log directory.
    ``[]`` (no sidecar at export time) is valid.
    """
    if version == 1:
        if "sigs" in bundle:
            return ("format_version 1 bundles must not carry a 'sigs' key "
                    "(signatures arrived with format version 2)")
        return None
    sigs = bundle.get("sigs")
    if not isinstance(sigs, list):
        return "format_version 2 bundles require 'sigs' to be a list of signature entries"
    for index, entry in enumerate(sigs):
        shape_error = signing.entry_shape_error(entry)
        if shape_error is not None:
            return f"sigs[{index}]: {shape_error}"
        if not signing.entry_is_valid(entry):
            return (
                f"sigs[{index}]: signature invalid for seq={entry.get('seq')} "
                f"(head {str(entry.get('head_hash'))[:12]}...) — the signature "
                "does not match its own seq/hash/key"
            )
    return None
