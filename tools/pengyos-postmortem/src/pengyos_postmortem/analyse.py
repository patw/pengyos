"""The rules: given journal entries, say what ended the session.

Pure functions over `journal.Entry`, no I/O, so every rule is testable with a
hand-built timeline.  The interesting rules are the ones that separate a
*broadcast* signal (one sender, many victims) from a crash, a logout, and an
out-of-memory kill — the four look identical to a person staring at a login
screen, and only the journal tells them apart.

Written after 2026-10-25 on behopc: a unit test called os.killpg(1, SIGTERM),
which Linux turns into kill(-1) — a SIGTERM to every process the user could
signal.  Every app died in one millisecond, systemd --user died with them, the
session ended and greetd put the login screen back.  Nothing rebooted, nothing
OOM-killed, nothing panicked: there was no fault to find, only a signal.

Every pattern here was checked against that boot's real journal, which is why
they are narrower than they look: `earlyoom` announces its *policy* with the
word SIGTERM in it, and the kernel registers hibernation memory at boot.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

from .journal import Entry

# ---------------------------------------------------------------- patterns ----

# A death has to be *announced*: a death verb next to a signal, or one of the
# three shapes a kernel/systemd uses. A bare "SIGTERM" is not a death —
# earlyoom logs "sending SIGTERM when mem <= 4.00%" as configuration — and a
# signal we do not know (systemd's "Received SIGRTMIN+20 from PID 494") is a
# notification, not a death.
_KNOWN_SIG = r"SIG(?:TERM|KILL|INT|HUP|QUIT|SEGV|ABRT|BUS|ILL|FPE)\b"
_DEATH = re.compile(
    rf"(?:exiting on|terminated (?:by|with|abnormally)|killed (?:by|with)|received|got)\b"
    rf"[^.;:]{{0,24}}?(?:{_KNOWN_SIG}|\bsignal\s+\d+)"
    r"|Killed process\s+\d+\s*\("          # kernel OOM killer
    r"|killed\s*\(\s*SIG[A-Z]+"            # "killed (SIGTERM)"
    r"|code=killed"                        # systemd unit exit
    r"|\bterminated abnormally\b",         # systemd-coredump
    re.IGNORECASE,
)

# Which signal killed it, from any of the spellings in the wild.
_SIG_NAME = re.compile(rf"({_KNOWN_SIG})", re.IGNORECASE)
_SIG_NUM = re.compile(r"\bsignal\s+(\d+)|status=(\d+)/|signal\s+\d+/([A-Z]+)", re.IGNORECASE)

_SIGNAL_NUMBERS = {"HUP": 1, "INT": 2, "QUIT": 3, "ILL": 4, "ABRT": 6, "BUS": 7,
                   "FPE": 8, "KILL": 9, "SEGV": 11, "TERM": 15}
# "Something ended me" vs "I fell over". SIGKILL is a termination, SIGSEGV is a crash.
_TERMINATIONS = {1, 2, 3, 9, 15}

# The victim, when the message names one rather than being logged by it.
_VICTIM_PID = re.compile(r"(?:Killed process|Process)\s+(\d+)\s*\(([^)]+)\)")
_VICTIM_QUOTED = re.compile(r"process '([^']+)'")
_VICTIM_UNIT = re.compile(r"^([\w@.:\-]+\.(?:service|scope)):\s")

_MANAGER_STOP = re.compile(r"Stopping User Manager for UID (\d+)")
_MANAGER_GONE = re.compile(r"user@(\d+)\.service: Deactivated successfully")
_SESSION_NEW = re.compile(r"New session '([^']+)' of user '([^']+)' with class '([^']+)'")
_SCOPE_STOP = re.compile(r"^\s*Stopped\s+\S+\.scope\.?$")

_EXCLUSIONS = {
    "out of memory": re.compile(
        r"oom-kill|Out of memory|Killed process|earlyoom.*(?:sending SIG|killing)|"
        r"invoked oom-killer", re.IGNORECASE),
    "kernel fault": re.compile(
        r"\bOops\b|kernel BUG at|kernel panic|general protection fault|"
        r"hardware error|Machine Check|MCE:", re.IGNORECASE),
    "gpu fault": re.compile(
        r"GPU HANG|gpu reset|GuC firmware|amdgpu.*(?:reset|ring .* timeout)|"
        r"NVRM: Xid|drm.*ring .*timeout|drm_WARN", re.IGNORECASE),
    # Entry/exit events only: the kernel also logs "PM: hibernation: Registered
    # nosave memory" at every boot, which is not a suspend.
    "suspend/resume": re.compile(
        r"PM: suspend (?:entry|exit)|PM: hibernation (?:entry|exit)|"
        r"Finishing wakeup|PM: Image loading (?:progress|done)|Suspending system",
        re.IGNORECASE),
    "watchdog": re.compile(
        r"watchdog: BUG|soft lockup|hard LOCKUP|task .* blocked for more than",
        re.IGNORECASE),
    "shutdown/reboot": re.compile(
        r"systemd\[1\]: (?:Shutting down|Reached target (?:System Reboot|System Power Off))|"
        r"reboot: (?:Restarting|Power down)|systemd-shutdown", re.IGNORECASE),
}

LIMITS = [
    "The kernel does not record who sent a signal. A broadcast is consistent with "
    "kill(-1) / killpg(1) / a too-broad pkill -f; this report cannot name the sender.",
    "Only processes that *announce* their death are counted, so the victim count is a "
    "lower bound: a process killed inside a cgroup usually says nothing.",
    "Correlate the timestamp with what was running then: shell history, app logs, "
    "agent transcripts.",
]


def interesting(message: str) -> bool:
    """Could any rule below use this message? The memory guard for big journals.

    A server journal is gigabytes of JSON and this tool runs on machines that are
    already in trouble, so the stream is filtered as it is read. The filter is
    built from the same patterns the rules use, and
    `test_every_rule_survives_the_memory_filter` pins one real line per rule — if a
    rule grows a pattern, that test fails until the filter grows it too, so a
    clean "no evidence" can never come from a dropped line.
    """
    if "logged out" in message.lower():
        return True
    if (_DEATH.search(message) or _SESSION_NEW.search(message)
            or _MANAGER_STOP.search(message) or _MANAGER_GONE.search(message)
            or _SCOPE_STOP.match(message)):
        return True
    return any(pattern.search(message) for pattern in _EXCLUSIONS.values())


# ------------------------------------------------------------------ shapes ----

@dataclass
class Victim:
    ts: float
    pid: int | None
    comm: str
    message: str
    termination: bool          # True = killed, False = crashed

    def as_dict(self) -> dict:
        return {"ts": self.ts, "pid": self.pid, "comm": self.comm,
                "termination": self.termination, "message": self.message}


@dataclass
class Burst:
    start: float
    end: float
    victims: list[Victim] = field(default_factory=list)

    @property
    def processes(self) -> list[tuple[int | None, str]]:
        """Distinct victims, as (pid, comm).

        A pid-less announcement ("process 'cosmic-bg' terminated") and a pid-ful
        one for the same name are the same process; keep the pid-ful form.
        """
        seen: dict[tuple[int | None, str], None] = {}
        for v in self.victims:
            seen.setdefault((v.pid, v.comm), None)
        named = {comm for pid, comm in seen if pid is not None}
        return [k for k in seen if k[0] is not None or k[1] not in named]

    @property
    def termination_signals(self) -> int:
        return sum(1 for v in self.victims if v.termination)

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "span_s": round(self.end - self.start, 3),
                "victims": [v.as_dict() for v in self.victims],
                "processes": [{"pid": p, "comm": c} for p, c in self.processes]}


@dataclass
class SessionFacts:
    boots: list[str] = field(default_factory=list)
    sessions_created: list[dict] = field(default_factory=list)
    sessions_ended: list[dict] = field(default_factory=list)
    managers_stopped: list[dict] = field(default_factory=list)   # asked to stop
    managers_gone: list[dict] = field(default_factory=list)      # deactivated

    @property
    def manager_signalled(self) -> list[dict]:
        """Deactivations with no stop request just before them.

        systemd logs "Stopping User Manager" when something *asks*.  A manager
        that deactivates without that line received a signal directly — which is
        what a broadcast to the session looks like from systemd's side.
        """
        out = []
        for gone in self.managers_gone:
            asked = any(
                s["uid"] == gone["uid"] and 0 <= gone["ts"] - s["ts"] <= 30
                for s in self.managers_stopped)
            if not asked:
                out.append(gone)
        return out

    def as_dict(self) -> dict:
        return {
            "boots": self.boots,
            "sessions_created": self.sessions_created,
            "sessions_ended": self.sessions_ended,
            "managers_stopped": self.managers_stopped,
            "managers_gone": self.managers_gone,
            "managers_signalled": self.manager_signalled,
        }


@dataclass
class Report:
    kind: str
    headline: str
    detail: list[str]
    bursts: list[Burst]
    session: SessionFacts
    exclusions: dict[str, list[Entry]]
    window: float
    min_victims: int

    def as_dict(self) -> dict:
        return {
            "verdict": {"kind": self.kind, "headline": self.headline,
                        "detail": self.detail},
            "bursts": [b.as_dict() for b in self.bursts],
            "session": self.session.as_dict(),
            "exclusions": {k: [e.message for e in v] for k, v in self.exclusions.items()},
            "limits": LIMITS,
            "window_s": self.window,
            "min_victims": self.min_victims,
        }


# ------------------------------------------------------------------ rules ----

def _signal_number(message: str) -> int | None:
    """The signal in a death message, as a number, or None if it names none."""
    m = _SIG_NAME.search(message)
    if m:
        # The pattern matches the whole token ("SIGSEGV"); the table is keyed by name.
        name = m.group(1).upper()
        name = name[3:] if name.startswith("SIG") else name
        if name in _SIGNAL_NUMBERS:
            return _SIGNAL_NUMBERS[name]
    m = _SIG_NUM.search(message)
    for group in (m.groups() if m else ()):
        if not group:
            continue
        return _SIGNAL_NUMBERS.get(group.upper()) if group.isalpha() else int(group)
    return None


def _program_name(raw: str) -> str:
    """cosmic-session quotes the whole exec line, environment and all.

    "process ' WAYLAND_DISPLAY=wayland-1 ... cosmic-osd '" — the name we want is
    the last token, without its directory or any VAR=value noise.
    """
    words = raw.replace("'", " ").split()
    for word in reversed(words):
        if "=" in word and "/" not in word:
            continue                      # an environment assignment
        return word.rsplit("/", 1)[-1][:64]
    return words[-1][:64] if words else "?"


def _victim(entry: Entry) -> Victim | None:
    """One death announcement -> Victim, or None if it is not one."""
    message = entry.message
    if not _DEATH.search(message):
        return None
    m = _VICTIM_PID.search(message)
    if m:
        pid: int | None = int(m.group(1))
        comm = m.group(2)
    else:
        m = _VICTIM_QUOTED.search(message)
        if m:
            pid, comm = None, _program_name(m.group(1))
        else:
            m = _VICTIM_UNIT.match(message)
            if m:
                pid, comm = None, m.group(1)
            else:
                pid, comm = entry.pid, entry.comm
    number = _signal_number(message)
    # No signal named at all (e.g. "code=killed, status=6/ABRT" has one; a bare
    # "terminated abnormally" does not) — treat it as a termination.
    termination = True if number is None else number in _TERMINATIONS
    return Victim(ts=entry.ts, pid=pid, comm=comm, message=message,
                  termination=termination)


def collect_bursts(entries: list[Entry], window: float = 2.0) -> list[Burst]:
    """Group death announcements that landed within `window` of each other."""
    events = [v for v in (_victim(e) for e in entries) if v is not None]
    events.sort(key=lambda v: v.ts)
    bursts: list[Burst] = []
    for victim in events:
        if bursts and victim.ts - bursts[-1].end <= window:
            bursts[-1].victims.append(victim)
            bursts[-1].end = victim.ts
        else:
            bursts.append(Burst(start=victim.ts, end=victim.ts, victims=[victim]))
    return bursts


def collect_session(entries: list[Entry]) -> SessionFacts:
    facts = SessionFacts()
    for entry in entries:
        if entry.boot_id and entry.boot_id not in facts.boots:
            facts.boots.append(entry.boot_id)
        msg = entry.message
        if (m := _SESSION_NEW.search(msg)):
            facts.sessions_created.append(
                {"ts": entry.ts, "session": m.group(1), "user": m.group(2), "class": m.group(3)})
        elif "logged out" in msg.lower() and "session" in msg.lower():
            facts.sessions_ended.append({"ts": entry.ts, "message": msg})
        if (m := _MANAGER_STOP.search(msg)):
            facts.managers_stopped.append({"ts": entry.ts, "uid": int(m.group(1))})
        if (m := _MANAGER_GONE.search(msg)):
            facts.managers_gone.append({"ts": entry.ts, "uid": int(m.group(1))})
    return facts


def collect_exclusions(entries: list[Entry]) -> dict[str, list[Entry]]:
    """Evidence for each alternative explanation; an empty list rules it out."""
    found: dict[str, list[Entry]] = {name: [] for name in _EXCLUSIONS}
    for entry in entries:
        for name, pattern in _EXCLUSIONS.items():
            if pattern.search(entry.message):
                found[name].append(entry)
    return found


def _scope_stops(entries: list[Entry], burst: Burst, slack: float = 5.0) -> int:
    """Scopes systemd stopped in the same moment — the silent majority of victims."""
    return sum(1 for e in entries
               if _SCOPE_STOP.match(e.message)
               and burst.start - 1.0 <= e.ts <= burst.end + slack)


def _broadcast(burst: Burst, min_victims: int) -> bool:
    return (len(burst.processes) >= min_victims and burst.termination_signals >= min_victims)


def _iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _names(processes: list[tuple[int | None, str]], limit: int = 12) -> str:
    shown = ", ".join(f"{c}({p})" if p is not None else c for p, c in processes[:limit])
    return shown + (" …" if len(processes) > limit else "")


def analyse(entries: list[Entry], window: float = 2.0,
            min_victims: int = 3) -> Report:
    session = collect_session(entries)
    exclusions = collect_exclusions(entries)
    bursts = collect_bursts(entries, window)

    broadcast = max(bursts, key=lambda b: len(b.processes), default=None)
    if broadcast is not None and not _broadcast(broadcast, min_victims):
        broadcast = None
    signalled = session.manager_signalled
    oom = exclusions["out of memory"]
    logout = bool(session.managers_stopped) and not signalled

    def _report(kind: str, headline: str, detail: list[str]) -> Report:
        if oom and kind != "out-of-memory":
            detail.append("Note: out-of-memory evidence is also present — check the "
                          "RULED OUT / FOUND list before trusting this verdict.")
        return Report(kind=kind, headline=headline, detail=detail, bursts=bursts,
                      session=session, exclusions=exclusions, window=window,
                      min_victims=min_victims)

    def _broadcast_detail(b: Burst) -> list[str]:
        detail = [
            f"{len(b.processes)} processes reported being terminated within "
            f"{b.end - b.start:.1f}s, starting {_iso(b.start)}.",
            "Victims: " + _names(b.processes),
        ]
        stops = _scope_stops(entries, b)
        if stops:
            detail.append(f"{stops} more scopes were stopped in the same moment — those "
                          "processes say nothing when a cgroup kills them.")
        return detail

    # Two independent fingerprints outrank any single one: many unrelated victims in
    # one instant AND a user manager that was never asked to stop.
    if signalled and broadcast is not None:
        detail = _broadcast_detail(broadcast)
        detail.append(
            "systemd --user for uid " + ", ".join(str(s["uid"]) for s in signalled)
            + " deactivated with no stop request — it received the signal too, so this "
            "was not a logout.")
        detail.append(
            "One sender, many unrelated victims, one instant: the fingerprint of a "
            "wildcard kill (kill(-1), killpg(1), a too-broad pkill -f), not a crash.")
        return _report("broadcast", "broadcast signal — one sender, many victims", detail)

    if signalled:
        return _report(
            "broadcast", "the session's user manager was signalled, not asked to stop",
            ["systemd --user for uid " + ", ".join(str(s["uid"]) for s in signalled)
             + " deactivated with no 'Stopping User Manager' line.",
             "That is the signature of a signal delivered to the manager itself — the "
             "session died from outside, it did not end on its own."])

    if logout:
        return _report(
            "logout", "a requested stop, not a fault",
            [f"systemd was asked to stop the user manager ({len(session.managers_stopped)} "
             "request(s)), which is what a logout or `loginctl terminate-user` looks like."])

    if oom:
        return _report(
            "out-of-memory", "out of memory",
            [f"{len(oom)} out-of-memory line(s); first: {oom[0].message[:160]}"])

    if broadcast is not None:
        detail = _broadcast_detail(broadcast)
        detail.append(
            "No user manager in the window, so there is no session record to confirm it — "
            "but one instant and many unrelated victims still points at a wildcard kill.")
        return _report("broadcast", "broadcast signal — one sender, many victims", detail)

    if bursts:
        biggest = max(bursts, key=lambda b: len(b.processes))
        return _report(
            "single-process",
            f"{len(biggest.processes)} process(es) ended, nothing else did",
            ["Victims: " + _names(biggest.processes),
             "No manager deactivation, no OOM, no fault: this looks like one process "
             "(or one app's tree) ending on its own."])

    return _report(
        "unknown", "nothing ended and nothing faulted in this window",
        ["No death announcements, no OOM, no kernel fault. Narrow the window with "
         "--since/--until, or point --boot at the right boot."])
