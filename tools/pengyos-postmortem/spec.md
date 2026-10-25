# pengyos-postmortem — spec

**Contract:** given a boot's journal, say what ended a session (or a burst of
processes), rule out the alternative explanations, and never guess.

Status: 0.1.0. Read-only. Standard library only. No root required.

## Why

Four causes produce the same symptom — "everything died and I got a login
screen" — and they need opposite fixes. See the table in `README.md`. The
distinguishing evidence exists in the journal but takes a long, error-prone
manual grep to extract, and the failure mode of getting it wrong is doing
expensive work on the wrong subsystem (memory, GPU, kernel) while the real cause
is a single `kill()`.

## Interface

```
pengyos-postmortem [--boot N] [--since T] [--until T]
                   [--window SECONDS] [--min-victims N] [--json] [--version]
```

- `--boot` — `journalctl` boot index: `0` this boot (default), `-1` previous, …
- `--since` / `--until` — `journalctl` time expressions; default is the whole boot.
- `--window` — seconds within which deaths are one burst. Default `2.0`.
- `--min-victims` — distinct processes in a burst before it can be a broadcast.
  Default `3`.
- `--json` — one JSON document on stdout; nothing else is printed.

Exit codes: `0` analysis produced (whatever the verdict), `2` the journal could
not be read (missing `journalctl`, empty journal, or no permission — the reason
goes to stderr).

## Rules (in priority order)

1. **Broadcast** — a burst (≥ `--min-victims` distinct processes inside
   `--window`) whose victims were *terminated* (SIGTERM/KILL/INT/HUP/QUIT), and
   a `user@<uid>.service: Deactivated successfully` with no `Stopping User
   Manager for UID <uid>` in the preceding 30 s. Two independent fingerprints;
   this is the strongest verdict the tool can give.
2. **Broadcast (manager only)** — the manager deactivation without a stop
   request, even if few processes announced themselves.
3. **Logout** — a `Stopping User Manager` request exists and no manager was
   signalled: a requested stop, not a fault.
4. **Out of memory** — OOM evidence present and no stronger signal verdict.
5. **Broadcast (burst only)** — a qualifying burst with no user session in the
   window (a headless machine, or another user).
6. **Single process** — deaths exist but the burst is too small to be a
   broadcast.
7. **Unknown** — nothing ended and nothing faulted; say so rather than guess.

When OOM evidence coexists with another verdict, the report says so
explicitly rather than hiding it.

## Victim identification

A victim is the process a death announcement is *about*, which is not always the
process that logged it:

| Journal shape | Victim |
|---|---|
| `exiting on signal 15` (logged by the process itself) | the logging process |
| `process ' NAME ' terminated with signal 15` | `NAME` (last token of the quoted exec line, basename if a path) |
| `process ' WAYLAND_DISPLAY=… /usr/bin/cosmic-comp ' terminated …` | `cosmic-comp` |
| `Killed process 51410 (cosmic-osd)` | `51410`, `cosmic-osd` |
| `Process 51410 (cosmic-osd) of user 1000 terminated abnormally with signal 6/ABRT` | `51410`, `cosmic-osd` |
| `cosmic-osd.service: Main process exited, code=killed, status=11/SEGV` | the unit name |

A pid-less announcement and a pid-ful one for the same name are one process.

## Not a death

- `earlyoom`: "sending SIGTERM when mem <= 4.00% …" — policy, not an event.
- `systemd`: "Received SIGRTMIN+20 from PID 494 (plymouthd)." — a notification.
- Any message with no death verb adjacent to a *known* signal.
- `Stopped foo.scope.` — a cgroup stop is counted separately as corroboration,
  never as a victim announcement (it is also how a normal app exit looks).

## Acceptance criteria

1. On the 2026-10-25 behopc boot the report says **broadcast**, names the
   victims, and reports no OOM / kernel fault / GPU reset / suspend / reboot.
2. The same pile of deaths *with* a `Stopping User Manager` line reports
   **logout** — the two cases must never be confused in either direction.
3. `--json` is valid JSON with the keys `tool`, `version`, `boot`, `verdict`,
   `bursts`, `session`, `exclusions`, `limits`, `window_s`, `min_victims`.
4. A journal that cannot be read exits `2` with a reason on stderr and prints
   nothing on stdout.
5. No test requires a journal, root, or network; `python3 -m pytest -q` passes
   offline.
6. Nothing is written to disk, no signal is sent, no state is changed.
