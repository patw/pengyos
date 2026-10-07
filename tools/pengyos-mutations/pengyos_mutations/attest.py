"""Local append-only, hash-chained event log for pengyos-mutations.

Every mutation Pengy applies is recorded here: one :func:`append_event` per
change, linked to its predecessor by a ``sha256`` hash chain so that any
modification, reordering, or truncation of a committed record is detectable by
:func:`verify_chain`.

The record schema and hash rule in this module are byte-for-byte compatible
with the canonical :mod:`pengyos_attest` package (the same ``seq`` / ``ts`` /
``type`` / ``data`` / ``prev_hash`` / ``hash`` fields and the same
``sha256(prev_hash || canonical(record-without-hash))`` rule). :mod:`.apply`
prefers the external ``pengyos-attest`` log when it is importable and falls
back to this local chain otherwise, so a chain produced by either implementation
verifies under the other.

A log is a plain JSONL file: one :func:`to_json_line` per line, in contiguous
``seq`` order. :func:`append_event` is atomic and crash-safe (temp file + fsync
+ rename) and holds an exclusive advisory lock (``<log>.lock``) across the
read-last-record → build → write critical section, so concurrent appends are
serialized and neither can clobber the other's write.

This module is intentionally a *local* chain: it does not carry the external
package's head-signature (ed25519) or Merkle-export machinery, which belong to
the signed ``pengyos-attest`` distribution. When that package is importable,
:mod:`.apply` uses it directly and this module is not on the hot path.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

__all__ = [
    "GENESIS_PREV_HASH",
    "RECORD_FIELDS",
    "LogError",
    "RecordError",
    "VerifyReport",
    "append_event",
    "chain_length",
    "dump_log",
    "from_json_line",
    "last_record",
    "new_record",
    "read_records",
    "record_hash",
    "to_json_line",
    "utc_now",
    "validate_record",
    "verify_chain",
]

#: ``prev_hash`` of the first (genesis) record in a chain.
GENESIS_PREV_HASH: str = "0" * 64

#: Required fields, in canonical (alphabetical) order.
RECORD_FIELDS = ("data", "hash", "prev_hash", "seq", "ts", "type")

#: ISO-8601 UTC with ``Z``; fractional seconds are optional.
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

#: Lowercase 64-hex-digit sha256 digest.
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

#: JSONL line format: keys sorted, compact separators, ASCII-only.
_JSONL = dict(sort_keys=True, separators=(",", ":"), ensure_ascii=True)

#: Suffix appended to a log's filename to name its exclusive-lock sidecar.
LOCK_SUFFIX: str = ".lock"


class RecordError(ValueError):
    """Raised when a record violates the schema or its hash rule."""


class LogError(Exception):
    """Raised when an append cannot be performed on a log file."""


@dataclass
class VerifyReport:
    """Structured outcome of :func:`verify_chain`.

    Attributes
    ----------
    ok:
        ``True`` when the chain passed every check that was run.
    record_count:
        Number of non-blank lines found in the file (0 for a missing/empty log).
    first_bad_seq:
        The ``seq`` of the first record that failed a check, or ``None``.
    first_bad_line:
        The 1-based file line number of the first failing non-blank line, or
        ``None``.
    reason:
        A human-readable explanation of the failure, or ``None`` when ``ok``.
    """

    ok: bool
    record_count: int
    first_bad_seq: Optional[int] = None
    first_bad_line: Optional[int] = None
    reason: Optional[str] = None


def utc_now() -> str:
    """The current UTC time in the canonical ``YYYY-MM-DDTHH:MM:SSZ`` form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: Any) -> str:
    """Deterministic JSON serialization used for hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_record(record: dict) -> str:
    """Canonical serialization of a record for hashing (excludes ``hash``)."""
    if not isinstance(record, dict):
        raise RecordError(f"record must be a JSON object, got {type(record).__name__}")
    body = {k: v for k, v in record.items() if k != "hash"}
    try:
        return canonical_json(body)
    except (TypeError, ValueError) as exc:
        raise RecordError(f"record is not JSON-serializable: {exc}") from exc


def record_hash(record: dict) -> str:
    """``hash = sha256(prev_hash || canonical(record-without-hash))``."""
    if not isinstance(record, dict):
        raise RecordError(f"record must be a JSON object, got {type(record).__name__}")
    prev_hash = record.get("prev_hash")
    if not isinstance(prev_hash, str):
        raise RecordError("record is missing a string 'prev_hash' field")
    payload = prev_hash.encode("ascii", errors="strict") + canonical_record(record).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RecordError(message)


def new_record(
    seq: int,
    ts: str,
    event_type: str,
    data: Any,
    prev_hash: str = GENESIS_PREV_HASH,
) -> dict:
    """Build a new, self-consistent record (schema + hash rule enforced)."""
    record: dict = {
        "seq": seq,
        "ts": ts,
        "type": event_type,
        "data": data,
        "prev_hash": prev_hash,
    }
    record["hash"] = record_hash(record)
    validate_record(record)
    return record


def validate_record(
    record: Any,
    *,
    expected_seq: Optional[int] = None,
    expected_prev_hash: Optional[str] = None,
) -> None:
    """Validate a record against the schema and hash rule.

    Checks, in order: container type; presence and types of all required
    fields; field constraints (1-based int ``seq``, ``Z``-suffixed ``ts``,
    non-empty ``type``, 64-hex ``prev_hash``/``hash``); optionally the exact
    expected ``seq`` and ``prev_hash`` (chain linkage); and finally that the
    ``hash`` equals the recomputed hash. Raises :class:`RecordError` on the
    first violation found.
    """
    _require(isinstance(record, dict), "record must be a JSON object")

    for field in RECORD_FIELDS:
        if field not in record:
            raise RecordError(f"record is missing required field {field!r}")

    seq = record["seq"]
    _require(isinstance(seq, int) and not isinstance(seq, bool), "seq must be an integer")
    if expected_seq is not None:
        _require(seq == expected_seq, f"seq {seq} != expected {expected_seq}")
    if expected_seq is None:
        _require(seq >= 1, f"seq must be >= 1, got {seq}")

    ts = record["ts"]
    _require(isinstance(ts, str) and _TS_RE.match(ts), f"ts must be ISO-8601 UTC with 'Z', got {ts!r}")

    etype = record["type"]
    _require(isinstance(etype, str) and etype, "type must be a non-empty string")

    data = record["data"]
    try:
        canonical_json(data)
    except (TypeError, ValueError) as exc:
        raise RecordError(f"data is not JSON-serializable: {exc}") from exc

    prev_hash = record["prev_hash"]
    _require(
        isinstance(prev_hash, str) and _HEX64_RE.match(prev_hash),
        f"prev_hash must be 64 lowercase hex chars, got {prev_hash!r}",
    )
    if expected_prev_hash is not None:
        _require(prev_hash == expected_prev_hash,
                 f"prev_hash {prev_hash} != expected {expected_prev_hash}")

    hash_ = record["hash"]
    _require(
        isinstance(hash_, str) and _HEX64_RE.match(hash_),
        f"hash must be 64 lowercase hex chars, got {hash_!r}",
    )

    recomputed = record_hash(record)
    _require(hash_ == recomputed, f"hash mismatch: record has {hash_}, recomputed {recomputed}")


def to_json_line(record: dict) -> str:
    """Serialize a record to one deterministic JSONL line (no trailing newline)."""
    return json.dumps(record, **_JSONL)


def from_json_line(line: str) -> dict:
    """Parse one JSONL line back into a record dict (schema-checked)."""
    if line.endswith("\n"):
        line = line[:-1]
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RecordError(f"line is not valid JSON: {exc}") from exc
    validate_record(record)
    return record


def read_records(log_path: "os.PathLike[str] | str") -> list:
    """Read and validate every record in the log, in file order.

    Blank lines are ignored. A missing/empty file yields an empty list. Any
    non-blank line that is not a valid record raises :class:`LogError`.
    """
    path = Path(log_path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    records: list = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(from_json_line(line))
        except RecordError as exc:
            raise LogError(f"line {lineno} is not a valid record: {exc}") from exc
    return records


def chain_length(log_path: "os.PathLike[str] | str") -> int:
    """The number of records in the log (0 for a missing/empty file)."""
    return len(read_records(log_path))


def last_record(log_path: "os.PathLike[str] | str") -> Optional[dict]:
    """The final record in the log, or ``None`` if the log is empty/missing."""
    records = read_records(log_path)
    return records[-1] if records else None


def lock_path(log_path: "os.PathLike[str] | str") -> Path:
    """Return the exclusive-lock sidecar path for a log: ``<log>.lock``."""
    path = Path(log_path)
    return path.with_name(path.name + LOCK_SUFFIX)


@contextlib.contextmanager
def _file_lock(log_path: "os.PathLike[str] | str") -> Iterator[None]:
    """Hold an exclusive advisory lock (POSIX ``flock``) for the duration."""
    try:
        import fcntl
    except ModuleNotFoundError:  # pragma: no cover - non-POSIX fallback
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


def _fsync_dir(directory: Path) -> None:
    try:
        dfd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


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
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _fsync_dir(directory)


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
    self-consistent record via :func:`new_record`, and writes it atomically.
    The whole read-last → build → write sequence runs under an exclusive file
    lock so concurrent appends are serialized. Raises :class:`LogError` for a
    corrupt existing log and :class:`RecordError` for invalid input.
    """
    path = Path(log_path)
    body_ts = ts if ts is not None else utc_now()
    with _file_lock(path):
        last = last_record(path)
        if last is None:
            seq, prev_hash = 1, GENESIS_PREV_HASH
        else:
            seq, prev_hash = last["seq"] + 1, last["hash"]
        record = new_record(seq, body_ts, event_type, data, prev_hash=prev_hash)

        existing = ""
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
        content = existing + to_json_line(record) + "\n"
        _atomic_write_text(path, content)
    return record


def _walk_records(lines: "list[str]") -> "tuple[VerifyReport, list]":
    """Walk file lines as chain records; return ``(report, passing_records)``."""
    records: list = []
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
                        f"{expected_seq} is expected (reordered or duplicate)"),
            ), records
        if raw["prev_hash"] != prev_hash:
            return VerifyReport(
                ok=False, record_count=len(records) + 1,
                first_bad_seq=raw["seq"], first_bad_line=lineno,
                reason=(f"line {lineno}: prev_hash {raw['prev_hash'][:12]}... does "
                        f"not link to the previous record's hash "
                        f"{prev_hash[:12]}... (chain broken)"),
            ), records
        records.append(raw)
        prev_hash = raw["hash"]
    return VerifyReport(ok=True, record_count=len(records)), records


def dump_log(
    store: "str | os.PathLike[str] | None" = None,
    *,
    json_out: bool = False,
    log_path: "str | os.PathLike[str] | None" = None,
) -> int:
    """Print the attest log.

    With ``json_out`` the raw records are emitted as a JSON array; otherwise a
    compact, human-readable one-line-per-record summary is printed. A missing
    or empty log prints ``[]`` (JSON) or ``no events`` (text) and returns 0.

    The log lives at ``<store>/events.jsonl``; ``store`` defaults to
    ``$PENGYOS_MUTATIONS_STORE`` or ``~/.config/pengyos/mutations``. Pass
    ``log_path`` to point at a specific file instead (used by tests).
    """
    if log_path is not None:
        path = Path(log_path)
    elif store is not None:
        path = Path(store).expanduser() / "events.jsonl"
    else:
        from pengyos_mutations.apply import default_store

        path = default_store() / "events.jsonl"
    records = read_records(path)
    if json_out:
        print(json.dumps(records, indent=2, sort_keys=True))
        return 0
    if not records:
        print("no events")
        return 0
    for rec in records:
        data = rec.get("data") or {}
        where = data.get("root", "")
        where = f" ({where})" if where else ""
        print(f"seq {rec['seq']} {rec['type']}{where} "
              f"exit={data.get('exit_code')} "
              f"hash={rec['hash'][:12]}…")
    return 0


def verify_chain(log_path: "os.PathLike[str] | str", *,
                 anchor: Optional[str] = None) -> VerifyReport:
    """Verify the hash chain at ``log_path`` and return a :class:`VerifyReport`.

    Never raises for corrupt content: every finding is returned in the report.
    Only I/O problems (``OSError``) propagate. An optional ``anchor`` (a
    previously committed head ``hash``) detects tail truncation.
    """
    path = Path(log_path)
    if not path.exists():
        if anchor is not None:
            return VerifyReport(
                ok=False, record_count=0,
                reason=f"truncated: anchored head {anchor[:12]}... not in an empty chain",
            )
        return VerifyReport(ok=True, record_count=0)
    text = path.read_text(encoding="utf-8")
    report, records = _walk_records(text.splitlines())
    if not report.ok:
        return report
    if anchor is not None and not records:
        return VerifyReport(
            ok=False, record_count=0,
            reason=f"truncated: anchored head {anchor[:12]}... not in an empty chain",
        )
    if anchor is not None and records[-1]["hash"] != anchor:
        return VerifyReport(
            ok=False, record_count=len(records),
            reason=(f"truncated: chain ends at seq {records[-1]['seq']} "
                    f"(hash {records[-1]['hash'][:12]}...) but the anchored head is "
                    f"{anchor[:12]}... — records after seq {records[-1]['seq']} were lost"),
        )
    return report
