# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.0] - 2026-11-17

### Added
- `pengyos-backup` (Server overlay, so every Server and Desktop): restic backups of
  jobs in `/etc/pengyos/backup.d/*.toml` to one repository per machine, each source
  read from a btrfs read-only snapshot inside a private mount namespace.
  `init` (password, SSH key for `sftp:`, recovery kit), `enable`/`disable` (and the
  timer), `run [--scheduled]` (retention and a monthly read-back check), `status`
  and `snapshots` (with `--json`), `restore` (to a new directory by default),
  `mount`, `check`, `kit`, `restic`. Failures alert through `pengyos-alert`.
- Two shipped jobs, off: `system` (the `/etc` overlay's changes and `/var/lib`) and
  `home` (`/home`, `/root`, minus caches, Trash, Steam libraries, `.nobackup` folders).
- Replaces the tar-based `pengyos-backup.sh` and `/etc/pengyos/backup.conf`.
