# Changelog — pengyos-alert

## [0.1.1] — 2026-11-17
### Fixed
- smartd's hook drops stdout: smartd logged "sent by ntfy" as unexpected
  output from its `-M exec` program (seen on a T470 with `-M test`).

## [0.1.0] — 2026-11-17
### Added
- `status`, `test`, `send`, `unit-failed`, `smartd`; ntfy and SMTP transports;
  secrets in a root-only `alert.cred`; per-key de-duplication (`REPEAT_HOURS`).
- Wiring: `pengyos-alert@.service`, `OnFailure=` on the Server's backup, monitor,
  update-check and unattended-update services, smartd's `-M exec` hook, and a
  disk-full check in pengyos-monitor.sh.
