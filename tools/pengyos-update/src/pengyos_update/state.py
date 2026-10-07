"""What the updater tells the rest of the system: saved check results, live
progress, and the login message.

* ``/var/lib/pengyos-update/state.json`` — written by ``check --save`` (the
  daily ``pengyos-update-check.timer``) and by ``apply``; world-readable. The
  desktop's update app and notifier read it; they never fetch anything
  themselves.
* ``/run/pengyos-update/progress.json`` — rewritten while ``apply --progress``
  runs (``pengyos-update-apply.service``): phase, overall fraction, message.
* ``/run/motd.d/50-pengyos-update`` — one line for SSH/console logins when an
  update is waiting (Server).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
from typing import Any

STATE = "/var/lib/pengyos-update/state.json"
PROGRESS = "/run/pengyos-update/progress.json"
MOTD = "/run/motd.d/50-pengyos-update"

# Overall progress: each phase owns a slice of the bar.
PHASES = {
    "start": (0.0, 0.02),
    "download": (0.02, 0.43),
    "write": (0.45, 0.40),
    "finish": (0.85, 0.15),
    "done": (1.0, 0.0),
    "error": (0.0, 0.0),
}


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: str | os.PathLike[str], obj: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o755)
    tmp = p.with_name(p.name + ".new")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


def load_state(path: str = STATE) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: str = STATE, **fields: Any) -> dict:
    state = load_state(path)
    state.update(fields)
    write_json(path, state)
    return state


def write_motd(text: str | None, path: str = MOTD) -> None:
    p = Path(path)
    try:
        if text:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        else:
            p.unlink(missing_ok=True)
    except OSError:
        pass  # a missing login message is never worth failing a check over


class Progress:
    """Phase + overall fraction, to a JSON file (if any) for the desktop app."""

    def __init__(self, path: str | None) -> None:
        self.path = path
        self.last = -1.0

    def update(self, phase: str, fraction: float = 0.0, message: str = "", **extra: Any) -> None:
        start, span = PHASES.get(phase, (0.0, 0.0))
        overall = round(start + span * max(0.0, min(1.0, fraction)), 4)
        if not self.path:
            return
        # Rewriting a file 100 times a second helps nobody.
        if phase not in ("done", "error") and abs(overall - self.last) < 0.005 and fraction not in (0.0, 1.0):
            return
        self.last = overall
        write_json(self.path, {"phase": phase, "fraction": overall, "message": message,
                               "time": now_iso(), **extra})
