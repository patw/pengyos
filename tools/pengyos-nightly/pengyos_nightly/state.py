"""state.json: what has been seen and triaged, so a report says only what's new."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

EMPTY = {"cves": {}, "findings": {}, "last_report": "", "actions": {},
         "patch_failures": {}}


def load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return json.loads(json.dumps(EMPTY))
    for k, v in EMPTY.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def save(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def finding_key(f: dict) -> str:
    """A finding is the same finding while its component and offered version are."""
    return f"{f['lane']}:{f['component']}:{f.get('available', '')}:{f.get('note', '')}"
