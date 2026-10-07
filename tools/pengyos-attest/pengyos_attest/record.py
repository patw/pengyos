"""Record schema and canonical JSON serialization for the hash chain.

A chain record is a JSON object with exactly these required fields::

    {"seq": int, "ts": str, "type": str, "data": <any JSON value>,
     "prev_hash": str, "hash": str}

* ``seq``       -- 1-based, contiguous sequence number of the record.
* ``ts``        -- event timestamp, ISO-8601 UTC with a ``Z`` suffix
                   (canonical form ``YYYY-MM-DDTHH:MM:SSZ``, fractional
                   seconds accepted).
* ``type``      -- non-empty event-type string (e.g. ``"build"``).
* ``data``      -- the event payload; any JSON-serializable value, ``null``
                   included.
* ``prev_hash`` -- the ``hash`` of the previous record, or
                   :data:`GENESIS_PREV_HASH` (``"0" * 64``) for the first
                   record.
* ``hash``      -- ``sha256`` over ``prev_hash`` (ASCII) concatenated with the
                   canonical serialization of the record **without** its
                   ``hash`` field (see :func:`canonical_record`).

Hash rule (the one and only definition; see ``spec.md``)::

    hash = sha256( record["prev_hash"].encode("ascii")
                   + canonical(record minus "hash").encode("utf-8") ).hexdigest()

Because ``prev_hash`` is itself a field of the record, the hash is a pure
function of the record alone: verification of the record's own integrity needs
no external state, and the chain-linkage check (``prev_hash`` of record N+1
equals ``hash`` of record N) is a separate, equally pure check.

Extra keys are tolerated (they participate in the hash through the canonical
serialization) so the schema can grow -- e.g. the optional ``sig`` field of a
future head-signing subtask -- without invalidating old records.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

__all__ = [
    "GENESIS_PREV_HASH",
    "RECORD_FIELDS",
    "RecordError",
    "canonical_json",
    "canonical_record",
    "record_hash",
    "new_record",
    "validate_record",
    "to_json_line",
    "from_json_line",
    "utc_now",
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


class RecordError(ValueError):
    """Raised when a record violates the schema or its hash rule."""


def utc_now() -> str:
    """The current UTC time in the canonical ``YYYY-MM-DDTHH:MM:SSZ`` form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: Any) -> str:
    """Deterministic JSON serialization used for hashing.

    Keys sorted, compact separators, non-ASCII escaped (``ensure_ascii``) so
    the byte sequence is identical on every platform and locale.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_record(record: Dict[str, Any]) -> str:
    """Canonical serialization of a record for hashing.

    The ``hash`` field itself is excluded (it is defined *by* this value, so
    including it would be circular). Every other key -- including any
    tolerated extras -- is included.
    """
    if not isinstance(record, dict):
        raise RecordError(f"record must be a JSON object, got {type(record).__name__}")
    body = {k: v for k, v in record.items() if k != "hash"}
    try:
        return canonical_json(body)
    except (TypeError, ValueError) as exc:
        raise RecordError(f"record is not JSON-serializable: {exc}") from exc


def record_hash(record: Dict[str, Any]) -> str:
    """Compute ``hash = sha256(prev_hash || canonical(record-without-hash))``."""
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
) -> Dict[str, Any]:
    """Build a new, self-consistent record (schema + hash rule enforced).

    ``prev_hash`` defaults to :data:`GENESIS_PREV_HASH` so
    ``new_record(1, ts, t, d)`` builds the genesis record. The returned dict
    always carries its own correct ``hash``; it is validated before return.
    """
    record: Dict[str, Any] = {
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
    ``hash`` field equals the recomputed hash. Raises :class:`RecordError`
    with a precise message on the first violation found.
    """
    _require(isinstance(record, dict), "record must be a JSON object")

    for field in RECORD_FIELDS:
        if field not in record:
            raise RecordError(f"record is missing required field {field!r}")

    seq = record["seq"]
    _require(isinstance(seq, int) and not isinstance(seq, bool), "seq must be an integer")
    _require(seq >= 1, f"seq must be >= 1, got {seq}")
    if expected_seq is not None:
        _require(seq == expected_seq, f"seq {seq} != expected {expected_seq}")

    ts = record["ts"]
    _require(isinstance(ts, str) and _TS_RE.match(ts), f"ts must be ISO-8601 UTC with 'Z', got {ts!r}")

    etype = record["type"]
    _require(isinstance(etype, str) and etype, "type must be a non-empty string")

    data = record["data"]
    # Every JSON value is legal payload; json.dumps will raise on the
    # non-serializable, which we surface as a RecordError.
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
    _require(hash_ == recomputed,
             f"hash mismatch: record has {hash_}, recomputed {recomputed}")


def to_json_line(record: Dict[str, Any]) -> str:
    """Serialize a record to one deterministic JSONL line (no trailing newline)."""
    return json.dumps(record, **_JSONL)


def from_json_line(line: str) -> Dict[str, Any]:
    """Parse one JSONL line back into a record dict (schema-checked)."""
    if line.endswith("\n"):
        line = line[:-1]
    try:
        record = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RecordError(f"line is not valid JSON: {exc}") from exc
    validate_record(record)
    return record
