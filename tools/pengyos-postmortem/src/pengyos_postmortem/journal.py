"""Read the systemd journal as plain records.

`journalctl -o json` is the only stable interface: the human format is already
rendered prose, and its wording changes between systemd releases.  Everything
here is parsing and subprocess plumbing so the analysis layer can stay pure and
testable without a journal on disk.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Entry:
    """One journal record, in the fields an autopsy actually needs."""

    ts: float                      # epoch seconds, microsecond precision
    pid: int | None                # _PID: who logged it
    comm: str                      # _COMM: their command name
    message: str
    unit: str | None = None        # _SYSTEMD_UNIT
    ident: str | None = None       # SYSLOG_IDENTIFIER
    boot_id: str | None = None
    priority: int = 6

    @property
    def who(self) -> str:
        return f"{self.comm}({self.pid})" if self.pid is not None else self.comm


class JournalError(RuntimeError):
    """journalctl could not be read (missing binary, or nothing to read)."""


def _text(value) -> str:
    """Decode a journald string field.

    Non-UTF-8 payloads arrive as an array of integers instead of a string; a
    message that cannot be decoded is still worth reporting, not discarding.
    """
    if isinstance(value, list):
        try:
            return bytes(b for b in value if isinstance(b, int)).decode("utf-8", "replace")
        except (TypeError, ValueError):
            return ""
    if value is None:
        return ""
    return str(value)


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_line(line: str) -> Entry | None:
    """One line of `journalctl -o json` -> Entry, or None if unusable."""
    line = line.strip()
    if not line:
        return None
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(rec, dict):
        return None
    micros = _int(rec.get("__REALTIME_TIMESTAMP"))
    if micros is None:
        return None
    return Entry(
        ts=micros / 1_000_000,
        pid=_int(rec.get("_PID")),
        comm=_text(rec.get("_COMM")) or _text(rec.get("SYSLOG_IDENTIFIER")) or "?",
        message=_text(rec.get("MESSAGE")),
        unit=_text(rec.get("_SYSTEMD_UNIT")) or None,
        ident=_text(rec.get("SYSLOG_IDENTIFIER")) or None,
        boot_id=_text(rec.get("_BOOT_ID")) or None,
        priority=_int(rec.get("PRIORITY")) if _int(rec.get("PRIORITY")) is not None else 6,
    )


def build_argv(boot: str = "0", since: str | None = None,
               until: str | None = None) -> list[str]:
    argv = ["journalctl", "--no-pager", "--quiet", "--output=json", f"--boot={boot}"]
    if since:
        argv += ["--since", since]
    if until:
        argv += ["--until", until]
    return argv


def read(boot: str = "0", since: str | None = None, until: str | None = None,
         keep=None) -> list[Entry]:
    """Collect the journal for one boot. Raises JournalError on failure.

    Streams rather than slurping: a busy server's journal is gigabytes of JSON,
    and this tool is meant to be safe to run *on the machine that is already in
    trouble*. `keep` is a predicate on the message — pass one to drop everything
    no rule could use (see `analyse.interesting`), which is what keeps a 2.5
    million line journal from becoming a 2 GB list of strings.
    """
    argv = build_argv(boot, since, until)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:                      # no journalctl at all
        raise JournalError(f"journalctl not found: {exc}") from exc
    except OSError as exc:
        raise JournalError(f"could not run journalctl: {exc}") from exc

    entries: list[Entry] = []
    seen = 0
    try:
        for line in proc.stdout:
            seen += 1
            entry = parse_line(line)
            if entry is not None and (keep is None or keep(entry.message)):
                entries.append(entry)
    finally:
        err = (proc.stderr.read() or "").strip().splitlines()
        proc.stderr.close()
        proc.stdout.close()
        proc.wait()

    if proc.returncode != 0 and not seen:
        raise JournalError(err[0] if err else f"journalctl exited {proc.returncode}")
    if not seen:
        window = f" between {since or 'boot'} and {until or 'now'}" if (since or until) else ""
        raise JournalError(
            f"journalctl returned no entries{window} — wrong --boot, an empty journal "
            "or window, or this user cannot read it (try root or the "
            "systemd-journal group)")
    # An empty list here means "the journal has lines, none of them relevant" —
    # that is a finding (nothing ended), not an error.
    return entries
