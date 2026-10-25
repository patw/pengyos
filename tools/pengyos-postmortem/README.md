# pengyos-postmortem

Why did that session end? One command, read-only, straight out of the journal.

```console
$ pengyos-postmortem
pengyos-postmortem 0.1.0 — boot 0, 2026-10-25 14:23:33 EDT → 16:42:06 EDT
6705 journal entries, 1 boot id(s)

VERDICT: broadcast signal — one sender, many victims
  15 processes reported being terminated within 0.6s, starting 2026-10-25 16:29:05 EDT.
  Victims: ssh-agent(962), cosmic-session(928), oo7-daemon(991), cosmic-launcher, …
  18 more scopes were stopped in the same moment — those processes say nothing when
  a cgroup kills them.
  systemd --user for uid 1000 deactivated with no stop request — it received the
  signal too, so this was not a logout.
  One sender, many unrelated victims, one instant: the fingerprint of a wildcard kill
  (kill(-1), killpg(1), a too-broad pkill -f), not a crash.

RULED OUT / FOUND
  out of memory      no evidence
  kernel fault       no evidence
  …
```

```console
$ pengyos-postmortem --json            # same analysis, machine-readable
$ pengyos-postmortem --boot -1         # the boot before this one
$ pengyos-postmortem --since "16:20" --until "16:35"
$ pengyos-postmortem --window 5 --min-victims 2
```

## Why this exists

"The desktop crashed back to a login screen and all my apps are dead" has four
completely different causes that look identical from the outside:

| Cause | What the journal shows | What it means |
|---|---|---|
| **Broadcast signal** | many unrelated processes announcing SIGTERM in the same instant, `systemd --user` deactivated with **no** `Stopping User Manager` line | one sender wiped out the session (`kill(-1)`, `killpg(1)`, a too-broad `pkill -f`) — nothing is broken, something *asked* |
| **Logout** | `Stopping User Manager for UID n` **then** the cascade | a requested stop, not a fault |
| **OOM** | `oom-kill`, `Out of memory`, `earlyoom … sending SIGTERM` | memory pressure, look for the hog |
| **Crash** | `code=killed, status=11/SEGV`, `terminated abnormally with signal 6/ABRT` | the process fell over on its own |

Reading that difference out of 6700 journal lines by hand takes twenty minutes
and a lot of grep, and the answer decides what you fix. This does it in one
command, and prints what it *ruled out* as prominently as what it found — a
clean "no OOM, no kernel fault, no GPU reset, no reboot" is evidence.

On 2026-10-25 it was the first row: a unit test called
`os.killpg(1, SIGTERM)`, which Linux turns into `kill(-1)` — a SIGTERM to every
process the user could signal. Fifteen processes announced their deaths inside
0.6 s, eighteen more scopes were stopped silently, `systemd --user` died
without ever being asked to, and greetd put the login screen back. Nothing
had rebooted, nothing was broken, and every generic "check your memory / your
GPU / your logs" instinct was a dead end.

## What it looks at

Only the journal, through `journalctl -o json`. It never writes, never signals
anything, and needs no privileges (`journalctl` read access is enough).

The stream is **filtered as it is read**, against the same patterns the rules
use, so a 2 GB server journal costs ~16 MB of RAM and a fraction of a second
instead of becoming a multi-gigabyte list of strings. A test pins one real
journal line per rule to that filter, so a clean "no evidence" can never come
from a dropped line.

- **Deaths** — a death verb next to a signal, `Killed process 1234 (firefox)`,
  `Process 51410 (cosmic-osd) … terminated abnormally with signal 6/ABRT`, or
  systemd's `code=killed, status=N/NAME`. Deliberately strict: `earlyoom`'s
  policy line contains the word SIGTERM and nobody died, and `Received
  SIGRTMIN+20 from PID 494` is a notification, not a death.
- **Bursts** — deaths grouped within `--window` seconds. A burst with at least
  `--min-victims` processes that were all *terminated* (not crashed) is a
  broadcast candidate.
- **The session** — `New session … of user …`, `logged out`, `Stopping User
  Manager for UID n` versus `user@n.service: Deactivated successfully`. The
  presence or absence of the stop request is what separates a logout from a
  signal delivered to the manager itself.
- **Exclusions** — OOM, kernel fault, GPU reset, suspend/resume, watchdog,
  shutdown/reboot. Each is reported as FOUND or "no evidence".

## Limits (printed in every report)

The kernel does not record **who** sent a signal, so this cannot name the
sender — only prove that a signal was the cause and that no fault was. It also
counts only processes that *announce* their death, so the victim count is a
lower bound; a process killed inside a cgroup usually says nothing. Correlate
the timestamp with what was running: shell history, app logs, agent
transcripts.

## Tests

```console
$ cd tools/pengyos-postmortem && python3 -m pytest -q
```

Every timeline in the suite is hand-built — no journal, no root, no network.
The patterns were checked against a real boot (the 2026-10-25 one, which is
where `earlyoom`'s SIGTERM policy line and the boot-time hibernation-memory line
became test cases).
