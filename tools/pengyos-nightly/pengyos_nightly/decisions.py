"""decisions.json — the operator's answers to what the nightly recommended.

The report says what upstream moved and what the loop refused; a refusal that says
"a human decision" is a dead end unless the human has somewhere to say yes. This file
is that place. `pengyos_nightly web` writes it (one click per recommendation) and the
planner reads it:

* **approved** — the planner considers the item in the `approved` lane, which lifts the
  two refusals that exist only to wait for a person (`lane_allowed` for a major lane,
  `major_never` for a series jump). Every other guard still runs: an approval cannot
  get past a cooldown, a missing fix, a dirty tree, a report-only component, or the
  testing-only channel.
* **denied** — the planner does not consider the item, and the page stops showing it,
  until upstream offers something outside what was denied (a new series).

An approval covers the version approved and later patch releases of the same series
(approve pengy 1.12.2, and 1.12.3 is what gets built if it has appeared since). A
denial covers the same span, so a stream of patch releases does not re-ask.

Keys are `<kind>:<component>`: `update` for a version a finding reported, `security`
for a CVE action the plan built. One decision per key; deciding again replaces it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from pengyos_nightly.versions import bump_kind

APPROVED, DENIED = "approved", "denied"


def key(kind: str, component: str) -> str:
    return f"{kind}:{component}"


def load(path: Path) -> dict:
    """{key: entry}. A missing or unreadable file is no decisions, not an error."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: Path, data: dict) -> None:
    """Atomic: the planner may read it at any moment."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


def decide(path: Path, kind: str, component: str, decision: str, target: str,
           now: dt.datetime | None = None, note: str = "") -> dict:
    if decision not in (APPROVED, DENIED):
        raise ValueError(f"decision must be {APPROVED!r} or {DENIED!r}, not {decision!r}")
    data = load(path)
    entry = {"kind": kind, "component": component, "decision": decision,
             "target": target, "at": (now or dt.datetime.now()).isoformat(timespec="seconds")}
    if note:
        entry["note"] = note
    data[key(kind, component)] = entry
    save(path, data)
    return entry


def undo(path: Path, kind: str, component: str) -> bool:
    data = load(path)
    gone = data.pop(key(kind, component), None)
    if gone is not None:
        save(path, data)
    return gone is not None


def covers(entry: dict | None, target: str) -> bool:
    """Does a decision made about `entry['target']` still speak for `target`?

    The same version, or a later patch release of the same series. A new series is a
    new question.
    """
    if not entry:
        return False
    decided = entry.get("target") or ""
    if not decided or not target or decided == target:
        return decided == target
    return bump_kind(decided, target) == "routine"


def lookup(data: dict, kind: str, component: str, target: str) -> str | None:
    """APPROVED, DENIED, or None when nothing decided covers this target."""
    entry = data.get(key(kind, component))
    return entry["decision"] if covers(entry, target) else None
