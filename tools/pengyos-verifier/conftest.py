"""Shared test helpers for pengyos-verifier tests.

Everything here is offline and root-free: fixtures are built in
``tmp_path`` from the in-tree format modules (which is exactly what
the witness does — it never trusts the producer tools). Integration
tests that need real system pieces stay behind ``PENGYOS_IT=1``.
"""

from __future__ import annotations

import json
from typing import Any, List, Mapping, Optional

import pytest

from pengyos_verifier import attest


def make_record(seq: int, ts: str, type_: str,
                data: Any, prev_hash: str) -> dict:
    """Build a schema-valid attest record with a correct self-hash."""
    record = {
        "seq": seq,
        "ts": ts,
        "type": type_,
        "data": data,
        "prev_hash": prev_hash,
    }
    record["hash"] = attest.record_hash(record)
    return record


def make_chain(records: List[Mapping[str, Any]] = ()) -> str:
    """Build a valid chain from scratch (genesis prev_hash) or continue.

    ``records`` is a list of (seq-ignored) dicts of
    ``{"ts": ..., "type": ..., "data": ...}``; each is appended with a
    contiguous seq and correct linkage. Returns the JSONL text.
    """
    prev = attest.GENESIS_PREV_HASH
    lines = []
    for i, spec in enumerate(records, start=1):
        rec = make_record(
            seq=i,
            ts=spec["ts"],
            type_=spec["type"],
            data=spec.get("data"),
            prev_hash=prev,
        )
        lines.append(json.dumps(rec, sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=True))
        prev = rec["hash"]
    return "\n".join(lines) + ("\n" if lines else "")


def it_enabled() -> bool:
    import os
    return bool(os.environ.get("PENGYOS_IT"))


def pytest_collection_modifyitems(config, items) -> None:
    skip = pytest.mark.skip(reason="integration test (PENGYOS_IT=1)")
    for item in items:
        if "integration" in item.keywords and not it_enabled():
            item.add_marker(skip)
