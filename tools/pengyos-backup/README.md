# pengyos-backup

Backups for every PengyOS machine: encrypted, deduplicated snapshots of chosen
folders to another machine, a disk or (later) a cloud bucket, over
[restic](https://restic.net). Each run reads the files from btrfs read-only
snapshots, so a backup is one consistent moment even with services running.
Shipped in the Server overlay as `/usr/bin/pengyos-backup` (one stdlib-only
Python script), so the Server, the Desktop and their ISOs all have it. The
contract is [spec.md](spec.md).

## Use

    # 1. Where the backups go (here: a directory on another machine over SSH)
    sudo pengyos-backup init --repo sftp:beholder@behoserver:/data/extra/backup/$(hostname)
    #    The first time, it prints an SSH key line to add to that account's
    #    ~/.ssh/authorized_keys (it may only use SFTP), then run init again.
    #    It ends with the RECOVERY KIT: the repository and its password. Keep it
    #    off this machine. Without the password nobody can read the backups.

    # 2. What to back up (two jobs ship, both off)
    sudo pengyos-backup enable system     # /etc changes + /var/lib
    sudo pengyos-backup enable home       # /home + /root, minus caches etc.

    # 3. It runs nightly at 02:30. Now, by hand:
    sudo pengyos-backup run
    pengyos-backup status

    # Getting things back
    sudo pengyos-backup snapshots home
    sudo pengyos-backup restore 1a2b3c4d --path /home/me/Documents/tax.pdf
    #   restores into /var/tmp/pengyos-restore-<time>/ (or --target DIR, --in-place)
    sudo pengyos-backup mount /mnt/backups   # browse every snapshot as folders

## Jobs

A job is a file in `/etc/pengyos/backup.d/`; its name is the file name.

```toml
enabled = true
paths = ["/home/me/Pictures", "/srv/media"]
exclude = ["*.tmp", "/home/me/Pictures/cache"]   # restic --exclude patterns
exclude_if_present = [".nobackup"]               # skip any folder holding this file
max_file_size = "8G"                             # skip single files larger than this
snapshot = true                                  # read from a btrfs snapshot
```

All jobs go into the machine's one repository (`/etc/pengyos/backup.toml`), tagged
`job:<name>`, so they share deduplication. Retention (`[keep]`, default 7 daily,
4 weekly, 12 monthly), the monthly read-back check (`[check]`) and when an alert
turns urgent (`[alert] stale_days`) are set there too.

## Repositories

| URL | Where |
|---|---|
| `sftp:user@host:/path` | any SSH server; `init` makes the key (`/etc/pengyos/backup/ssh/`) |
| `/path` | a local or USB disk, not the one being backed up |
| `s3:host/bucket/path` | S3, or Backblaze B2 through its S3 API; keys as `KEY=VALUE` lines in `/etc/pengyos/backup/env` (0600) |

An `sftp:` login can delete what it wrote, so a compromised machine could delete
its own backups. Use a restricted key (the line `init` prints), and for B2 turn on
the bucket's version history.

## For programs

`status --json`:

```json
{"host": "behothinkpad", "repository": "sftp:...", "last_check": "2026-11-17T03:10:00Z",
 "last_check_ok": true,
 "jobs": [{"name": "home", "enabled": true, "paths": ["/home", "/root"], "ok": true,
           "last_run": "...", "last_success": "...", "age_hours": 7.5,
           "snapshot": "1a2b3c4d...", "bytes_added": 104857600, "error": null}]}
```

`snapshots [JOB] --json` is restic's own `snapshots --json`.

## Files

`/etc/pengyos/backup.toml`, `/etc/pengyos/backup.d/*.toml`, the secrets in
`/etc/pengyos/backup/` (0600, refused otherwise), state in
`/var/lib/pengyos-backup/state.json`, restic's cache in `/var/cache/pengyos-backup`.
The units are `pengyos-backup.service` and `pengyos-backup.timer` (off until a job
is enabled).

## Tests

    cd tools/pengyos-backup && python3 -m pytest -q

Fakes stand in for restic, btrfs, unshare, mount, systemctl and pengyos-alert; with
`restic` on PATH, one test runs the real thing against a local repository.
