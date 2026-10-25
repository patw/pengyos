# Changelog — pengyos-postmortem

All notable changes to this tool. It keeps its own semver; only images use the
root `VERSION`.

## [0.1.0] — 2026-10-25

### Added

- `pengyos-postmortem`: read a boot's journal and say what ended a session.
  Verdicts: broadcast signal, logout, out of memory, single process, unknown.
  `--boot`, `--since`/`--until`, `--window`, `--min-victims`, `--json`.
- Rules for separating a **broadcast** (one sender, many victims) from a
  **crash**, a **logout** and an **OOM kill**, which look identical from the
  outside. The decisive evidence is a `user@<uid>.service: Deactivated
  successfully` with no `Stopping User Manager for UID <uid>` before it: a
  manager that was never asked to stop was signalled.
- Victim identification for the four shapes a real journal uses: self-reported
  signals, `process ' … '` (including a quoted exec line with its whole
  environment), `Killed process N (name)`, and systemd's `code=killed,
  status=N/NAME` keyed by unit.
- Exclusion rules for OOM, kernel fault, GPU reset, suspend/resume, watchdog and
  shutdown/reboot, each reported as FOUND or "no evidence" — a clean negative is
  part of the answer.
- Writes into the image by `board/server/post-build.sh` (Server and Desktop), and
  documented in the root `CHANGELOG.md`.

### Written because

On 2026-10-25 a PengyTasker unit test called `os.killpg(1, SIGTERM)`. On Linux
that is `kill(-1)`: SIGTERM to every process the user could signal. Fifteen
processes announced their deaths within 0.6 s, eighteen more scopes were stopped
silently, `systemd --user` died without being asked, and greetd restored the
login screen. No reboot, no OOM, no kernel fault, no GPU reset — every generic
lead was a dead end, and the distinguishing evidence was four lines in a
6705-line journal. This tool turns that twenty-minute reconstruction into one
command, and encodes the distinction so the next diagnosis starts from it.
