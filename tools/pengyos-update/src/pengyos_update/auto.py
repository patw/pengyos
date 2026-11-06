"""Unattended updates: the opt-in policy (``pengyos-update auto``).

Off unless an administrator turns it on (``auto = on`` in update.conf, and
``pengyos-update-auto.timer`` enabled). The run installs the newest release on
the machine's channel that is newer than the running one, has not failed here,
and was first seen at least ``auto_soak_days`` days ago (the feed carries no
dates, so the soak is measured from this machine's first sighting, which the
daily check records). It reboots only after an apply that succeeded.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from .release import CHANNELS, FeedEntry, UpdateError, parse_version
from .trial import failure

TIMER = "pengyos-update-auto.timer"
TIMER_DROPIN = "/etc/systemd/system/pengyos-update-auto.timer.d/50-pengyos-time.conf"
DEFAULT_AT = "04:00"
DEFAULT_SOAK_DAYS = 3
_AT_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_at(text: str) -> str:
    m = _AT_RE.match(text.strip())
    if not m:
        raise UpdateError(f"not a time of day: {text!r} (use HH:MM, 24-hour)")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def parse_soak(text: str | int) -> int:
    try:
        days = int(text)
    except (TypeError, ValueError):
        raise UpdateError(f"not a number of days: {text!r}") from None
    if days < 0 or days > 365:
        raise UpdateError(f"soak must be 0-365 days, not {days}")
    return days


def settings(cfg: dict[str, str]) -> dict:
    """The auto settings from update.conf, with defaults; a bad value is the default."""
    try:
        at = parse_at(cfg.get("auto_at", DEFAULT_AT))
    except UpdateError:
        at = DEFAULT_AT
    try:
        soak = parse_soak(cfg.get("auto_soak_days", DEFAULT_SOAK_DAYS))
    except UpdateError:
        soak = DEFAULT_SOAK_DAYS
    return {"enabled": cfg.get("auto", "off").strip().lower() in ("on", "yes", "true", "1"),
            "at": at, "soak_days": soak}


def timer_dropin(at: str) -> str:
    return ("# Written by `pengyos-update auto on --at HH:MM`; remove it with `auto off`.\n"
            "[Timer]\nOnCalendar=\n"
            f"OnCalendar=*-*-* {at}:00\n")


def _utc(iso: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _on_channel(entries: list[FeedEntry], image: str, channel: str) -> list[FeedEntry]:
    accept = CHANNELS.get(channel)
    if accept is None:
        raise UpdateError(f"unknown channel {channel!r} (use one of: {', '.join(CHANNELS)})")
    return [e for e in entries if e.image == image and set(e.channels) & set(accept)]


def record_seen(seen: dict, entries: list[FeedEntry], image: str, channel: str,
                now: dt.datetime) -> dict:
    """``seen`` with every release on the channel stamped the first time it appears.

    Versions no longer on the channel are dropped, so the record stays small."""
    out = {}
    for e in _on_channel(entries, image, channel):
        out[e.version] = seen.get(e.version) or _iso(now)
    return out


def pick(entries: list[FeedEntry], image: str, channel: str, current: str, state: dict,
         seen: dict, soak_days: int, now: dt.datetime) -> tuple[FeedEntry | None, FeedEntry | None, str | None]:
    """(what to install, what the run waits for, when that becomes eligible).

    The newest release that is newer than ``current``, not failed here, and
    soaked; and if something newer is still soaking, that one and its date."""
    cur = parse_version(current)
    newer = sorted((e for e in _on_channel(entries, image, channel)
                    if parse_version(e.version) > cur and not failure(state, e.version)),
                   key=lambda e: parse_version(e.version), reverse=True)
    soak = dt.timedelta(days=soak_days)
    waiting, until = None, None
    for e in newer:
        first = _utc(seen.get(e.version, "")) or now
        if now - first >= soak:
            return e, waiting, until
        if waiting is None:
            waiting, until = e, _iso(first + soak)
    return None, waiting, until


def write_timer_dropin(at: str, path: str = TIMER_DROPIN) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".new")
    tmp.write_text(timer_dropin(at), encoding="utf-8")
    tmp.replace(p)
