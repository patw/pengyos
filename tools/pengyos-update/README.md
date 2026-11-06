# pengyos-update

The on-device updater for [PengyOS](../../README.md): read the update
feed, verify the signed release, write it into the **other** root slot, boot it
once, and keep it only if it comes up healthy. Optionally (off by default),
do all of that unattended in a nightly window.

```console
$ pengyos-update status              # version, channel, both slots (as recorded at boot; sudo reads them live)
$ pengyos-update check               # is there a newer release on my channel?
$ sudo pengyos-update apply --reboot # install it and restart into it
$ sudo pengyos-update rollback       # make the other slot the default again
$ sudo pengyos-update channel testing
$ pengyos-update auto                # unattended updates: setting, last run, failures
$ sudo pengyos-update auto on --at 04:00 --soak 3
```

`pengyos-update confirm --boot` runs at boot (`pengyos-update-confirm.service`):
on a trial boot it runs the health checks and makes the slot the default only
if they pass. `spec.md` is the contract.

## How it works

The disk layout is `docs/state-layout.md`'s: ESP (GRUB + one kernel per slot),
`root-a`, `root-b` (read-only ext4), `data` (btrfs, the machine's state).

1. **Feed** — `https://pengyos.catbee.ca/update/feed.xml` (Atom, see
   `/etc/pengyos/update.conf`). Each entry names an `IMAGE_ID`, a `YY.MM.N`
   version, its channels and its manifest. The feed is *not* trusted.
2. **Manifest** — `manifest.json` (format `pengyos-update-1`) lists the rootfs
   (`rootfs.ext4.zst`: compressed and raw size + sha256) and the kernel
   (`bzImage`). Its detached `manifest.json.sig` is `pengyos-sig-v1` —
   `pengyos-sign`'s format, ed25519 over the SHA-256 — and must verify against a
   key in `/usr/share/pengyos/keys/` (checked with the `openssl` CLI). The
   manifest must name this machine's `IMAGE_ID` (no Desktop ↔ Desktop NVIDIA
   crossovers) and a newer version (no downgrades unless `--version` asks).
3. **Write** — downloads are resumable (`/var/cache/pengyos-update`) and each is
   hash-checked. The rootfs is unpacked onto the other slot while hashing; the
   kernel goes to `ESP/slot-X/bzImage`; `grub.cfg` is rewritten (both slots,
   the installed kernel arguments) with `pengyos_install`'s renderer.
4. **Boot once** — grubenv `pengyos_try=X`. GRUB clears it and boots slot X
   with `pengyos.trial=1`; if X reaches multi-user and its **health checks**
   pass, `confirm --boot` sets `pengyos_slot=X`. The checks are the
   executables in `/usr/lib/pengyos/update-health.d/` (the image's:
   `10-critical-units`: nftables, sshd and networkd active if enabled) and
   `/etc/pengyos/update-health.d/` (the machine's; same name replaces, a
   `/dev/null` symlink disables), retried for `health_timeout` seconds
   (`update.conf`, default 180). If they fail, the machine restarts, into the
   old slot. A trial boot must be confirmed before the next `apply` (so the
   fallback is never overwritten); `confirm` by hand keeps it without checks.
5. **A trial always ends** — a panic restarts after 10 s
   (`CONFIG_PANIC_TIMEOUT`), systemd pets the hardware watchdog
   (`RuntimeWatchdogSec=60s`), emergency/rescue mode restarts on a trial boot,
   and `pengyos-update-deadline.service` restarts a trial not confirmed after
   15 minutes. Each of those restarts lands on the old slot.
6. **Failures are remembered** — the trial is recorded in `state.json`; a trial
   that failed its checks, or that the old slot finds was booted and never
   confirmed, goes into `failed`. `apply` refuses a failed version unless named
   with `--version`, `auto-run` never picks it, and the login message says so.

## Unattended updates

Off by default; `sudo pengyos-update auto on [--at HH:MM] [--soak DAYS]` turns
them on (`auto = on`, `auto_at`, `auto_soak_days` in `update.conf`, a timer
drop-in, `pengyos-update-auto.timer` enabled). Each day at that time,
`auto-run` installs the newest release on the machine's channel that is newer,
not failed here, and was first seen at least the soak period ago (default 3
days; the feed has no dates, so the daily check records first sightings), then
restarts, but only if the install succeeded. No random delay, not
`Persistent`: a machine that was off then waits for the next window. On the
`testing` channel this is most nights. `sudo pengyos-update auto off` stops it.

Runtime: Python standard library, plus `curl`, `zstd`, `openssl`, e2fsprogs,
`lsblk`/`blockdev` and `grub-editenv` from the image.

## Publishing

`pengyos-release update-publish --build <buildroot output> --site <web root>
--key <pengyos-key-v1 private key> --channel testing` writes the release and
regenerates `feed.xml` + `index.html`; `update-promote` adds a channel.

## Tests

```console
python3 -m pytest -q
```
