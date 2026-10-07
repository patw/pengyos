"""The PengyOS attest record format, independently redefined.

The verifier is an *external witness*: it re-verifies the attest log
with its own code rather than trusting ``pengyos-attest`` to have done
so. This module is the one definition of the record format it uses —
the record schema, the canonical JSON rule, the hash rule, and the
chain-walk semantics. It interoperates with ``pengyos-attest`` (the
two tools share the same canonical form, so a chain produced there
verifies here), and ``tests/test_attest_format.py`` pins the
known-answer vectors of ``pengyos-attest``'s spec to prove it.

Hash rule (the single definition in this tool):

    canonical(record) = json.dumps(record minus "hash",
                                   sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=True)
    hash = sha256(record["prev_hash"].encode("ascii")
                  + canonical(record).encode("utf-8")).hexdigest()

Genesis record: ``prev_hash = "0" * 64`` (:data:`GENESIS_PREV_HASH`).
Because ``prev_hash`` is a field of the record, ``hash`` is a pure
function of the record alone: self-integrity needs no external state,
and chain linkage (record N+1's ``prev_hash`` == record N's ``hash``)
is a separate check performed by the walk.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

#: prev_hash of the first (genesis) record.
GENESIS_PREV_HASH = "0" * 64

#: Required record fields, in the order reported in schema errors.
REQUIRED_FIELDS: tuple = ("seq", "ts", "type", "data", "prev_hash", "hash")

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
# Canonical ts: YYYY-MM-DDTHH:MM:SSZ (fractional seconds allowed).
# The schema pins the FORMAT; calendar plausibility is not validated
# (same contract as pengyos-attest).
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

# Known-answer vectors pinned in pengyos-attest's spec (record schema
# and hash rule, fixed 2026-09-14). They pin interoperability: any
# change to the canonical form or the hash rule breaks them.
KAV_GENESIS: Mapping[str, Any] = {
    "seq": 1,
    "ts": "2026-01-01T00:00:00Z",
    "type": "build",
    "data": {"commit": "abc123"},
    "prev_hash": GENESIS_PREV_HASH,
    "hash": "5d133068004e620a7c8da3d15b1b927b5aae0ff08e6361452b98e9d674832347",
}
KAV_SECOND_HASH = (
    "38eb473499a5f12025106c37f875ace960bdc064a07a320251c0436b67d02882"
)


def canonical_json(record: Mapping[str, Any]) -> str:
    """The canonical JSON form of a record: everything except ``hash``,
    sorted keys, compact separators, ASCII-escaped.

    Extra keys are included (forward-compat: a future ``sig`` field
    participates in the hash); mutating or adding any key changes the
    canonical form and therefore invalidates the hash.
    """
    body = {k: v for k, v in record.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)


def record_hash(record: Mapping[str, Any]) -> str:
    """Recompute a record's ``hash`` from its own fields (see module doc)."""
    prev_hash = record.get("prev_hash", "")
    if not isinstance(prev_hash, str):
        prev_hash = str(prev_hash)
    payload = prev_hash.encode("ascii", "replace") + canonical_json(
        record
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def record_schema_errors(record: Mapping[str, Any]) -> List[str]:
    """The schema errors of one parsed record, in a stable order.

    Checks, in order: the six required fields are present; ``seq`` is a
    real int (a JSON ``bool`` is NOT a valid seq); ``ts`` matches the
    canonical ISO-8601 UTC form; ``type`` is a non-empty string;
    ``data`` is any JSON value (always valid); ``prev_hash`` and
    ``hash`` are 64 lowercase hex chars. Returns ``[]`` when the record
    is schema-valid. ``data`` has no validity rule of its own — a
    presence-only check (``null`` is valid) — so it never yields an
    error here; its type is whatever JSON gave us.
    """
    errors: List[str] = []
    for field_name in REQUIRED_FIELDS:
        if field_name not in record:
            errors.append(f"missing field {field_name!r}")
    if "seq" in record:
        seq = record["seq"]
        if isinstance(seq, bool) or not isinstance(seq, int):
            errors.append(
                f"seq must be an integer (bool is not valid), got "
                f"{type(seq).__name__}"
            )
    if "ts" in record:
        ts = record["ts"]
        if not isinstance(ts, str) or not _TS_RE.match(ts):
            errors.append(
                "ts must be an ISO-8601 UTC string ending in 'Z' "
                "(canonical YYYY-MM-DDTHH:MM:SSZ), got "
                f"{ts!r}"
            )
    if "type" in record:
        if not isinstance(record["type"], str) or not record["type"]:
            errors.append("type must be a non-empty string")
    for field_name in ("prev_hash", "hash"):
        if field_name in record:
            value = record[field_name]
            if not isinstance(value, str) or not _HEX64_RE.match(value):
                errors.append(
                    f"{field_name} must be 64 lowercase hex chars, got "
                    f"{value!r}"
                )
    return errors


def _is_nonblank_line(line: str) -> bool:
    return bool(line.strip())


@dataclass(frozen=True)
class AttestReport:
    """Result of the chain walk.

    ``ok``            True iff every walked record passed.
    ``record_count``  number of records parsed and walked (0 for an
                      empty or missing log).
    ``first_bad_seq`` the seq of the first failing record, or None when
                      the failing line is not a parseable record (or
                      the chain is clean).
    ``first_bad_line`` 1-based file line number of the first failure,
                      or None when clean.
    ``reason``        None when clean, else a short reason such as
                      ``"not valid JSON"``, ``"schema: <first error>"``,
                      ``"self-hash mismatch"``, ``"seq not contiguous"``
                      or ``"prev_hash linkage broken"``.
    """

    ok: bool
    record_count: int
    first_bad_seq: Optional[int]
    first_bad_line: Optional[int]
    reason: Optional[str]


def verify_attest_text(text: str) -> AttestReport:
    """Walk the attest log content in memory and report the first failure.

    Walks each NON-BLANK line in file order (blank lines are skipped,
    matching how the log is written atomically line-by-line); for each
    line: JSON parse -> record schema -> self-hash recompute -> seq
    contiguity from 1 -> prev_hash linkage (first record against the
    genesis value). The walk STOPS at the first failing record, so a
    clean prefix followed by corruption reports the corruption, not
    "everything is fine up to EOF". An empty or all-blank log is a
    valid empty chain (``ok=True, record_count=0``).
    """
    expected_seq = 1
    previous_hash: Optional[str] = None
    count = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not _is_nonblank_line(line):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            return AttestReport(False, count, None, line_no, "not valid JSON")
        if not isinstance(record, Mapping):
            return AttestReport(
                False, count, None, line_no,
                "record is not a JSON object",
            )
        schema_errors = record_schema_errors(record)
        if schema_errors:
            seq = record.get("seq")
            first_bad_seq = seq if isinstance(seq, int) and \
                not isinstance(seq, bool) else None
            return AttestReport(
                False, count, first_bad_seq, line_no,
                "schema: " + schema_errors[0],
            )
        if record_hash(record) != record["hash"]:
            return AttestReport(
                False, count, record["seq"], line_no,
                "self-hash mismatch",
            )
        if record["seq"] != expected_seq:
            return AttestReport(
                False, count, record["seq"], line_no,
                "seq not contiguous",
            )
        expected_seq += 1
        expected_prev = GENESIS_PREV_HASH if previous_hash is None \
            else previous_hash
        if record["prev_hash"] != expected_prev:
            return AttestReport(
                False, count, record["seq"], line_no,
                "prev_hash linkage broken",
            )
        previous_hash = record["hash"]
        count += 1
    return AttestReport(True, count, None, None, None)
