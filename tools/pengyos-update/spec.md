# pengyos-update — the on-device A/B updater

## Summary
Reads the signed update feed, writes a newer release into the *other* root
slot, boots it once, and keeps it only if it comes up **and passes its health
checks**. A trial that hangs, panics, drops to emergency mode or fails a check
ends up back on the old slot without anyone touching the machine. That
version is then remembered as failed. Optionally, and only when an
administrator turns it on, the updater installs stable releases unattended in
a fixed maintenance window.

## Purpose
Installed PengyOS machines (Server and Desktop) update from
`https://pengyos.catbee.ca/update/feed.xml`. Servers are headless homelab
boxes, and one of them is the house router (`~/Personal/Research/pengyos-router-plan.md`).
Ubuntu's unattended-upgrades is the model: updates arrive without anyone
logging in. On an image-based system that means applying and rebooting, so it
is only safe if a bad image always finds its way back. Version 0.2 had the
A/B slots and the one-shot GRUB trial, but:

1. a trial was kept as soon as `multi-user.target` was reached, even with
   `nftables.service` failed (no firewall at all, on a router);
2. the fallback needs another boot, and a kernel panic, a hung boot or
   emergency mode never gave one (`CONFIG_PANIC_TIMEOUT=0`, no watchdog);
3. nothing remembered a failed version, so a timer would retry it every night;
4. there was no unattended mode, by design ("installing is always the user's
   choice"). It stays the user's choice: unattended updates are opt-in.

## Interface
```
pengyos-update status [--json]
pengyos-update check [--save] [--json] [--quiet] [--exit-code] [--channel C]
sudo pengyos-update apply [--reboot] [--version YY.MM.N] [--channel C] [--progress [FILE]]
sudo pengyos-update confirm [--boot]
sudo pengyos-update rollback [--reboot]
sudo pengyos-update channel [NAME]
pengyos-update auto                                  # show the setting and the last run
sudo pengyos-update auto on [--at HH:MM] [--soak DAYS]
sudo pengyos-update auto off
sudo pengyos-update auto-run                          # the timer's job (pengyos-update-auto.service)
```
Errors exit 1 with one `pengyos-update: <message>` line; usage errors exit 2.

## Deliverable
| File | Ships at |
|---|---|
| `tools/pengyos-update/src/pengyos_update/` | `/usr/lib/python3*/site-packages/pengyos_update` (Server layer post-build) |
| `board/server/rootfs-overlay/etc/systemd/system/pengyos-update-*.service/.timer` | the existing check/apply/confirm/rollback/channel units |
| `board/server/rootfs-overlay/usr/lib/systemd/system/pengyos-update-auto.{service,timer}` | the unattended run, preset-disabled |
| `board/server/rootfs-overlay/usr/lib/systemd/system/pengyos-update-deadline.service` | trial boots only: reboot if not confirmed in time |
| `board/server/rootfs-overlay/usr/lib/systemd/system/{emergency,rescue}.service.d/pengyos-update-trial.conf` | trial boots only: reboot instead of a console prompt |
| `board/server/rootfs-overlay/usr/lib/pengyos/update-trial-reboot` | the shell helper those two use |
| `board/server/rootfs-overlay/usr/lib/pengyos/update-health.d/*` | the image's health checks |
| `board/server/rootfs-overlay/etc/pengyos/update-health.d/README` | where a machine adds its own |
| `board/server/rootfs-overlay/usr/lib/systemd/system.conf.d/60-pengyos-watchdog.conf` | `RuntimeWatchdogSec=` |
| `board/server/linux.fragment` | `CONFIG_PANIC_TIMEOUT=10` |
| `tools/pengyos-install/…/image.py` `render_grub_cfg` | `pengyos.trial=1` on a trial boot's kernel line |

## Behaviour

### Feed, manifest and apply (unchanged from 0.2)
1. The feed is untrusted. Each entry names an `IMAGE_ID`, a `YY.MM.N` version, its channels and its
   manifest. `stable` takes stable entries; `testing` takes testing and stable.
2. The manifest (`pengyos-update-1`) must verify (ed25519, `pengyos-sig-v1`)
   against a key in `/usr/share/pengyos/keys/`, name this machine's `IMAGE_ID`
   and the feed's version, and be newer unless `--version` asks.
3. `apply` writes the other slot (hashing what it writes), its kernel, runs
   the new image's `/usr/lib/pengyos/update.d/*` (fail closed), rewrites
   `grub.cfg`, and sets grubenv `pengyos_slot=<booted> pengyos_try=<other>`.
   It refuses while the running boot is itself an unconfirmed trial.

### Trials are recorded
4. A successful `apply` records `trial = {version, from, slot, at}` in
   `/var/lib/pengyos-update/state.json` (on the data partition, so both slots
   see it). `rollback` removes it.

### Health-gated confirm (`confirm --boot`)
5. `pengyos-update-confirm.service` runs `confirm --boot` after
   `multi-user.target`. If the running slot is already the default, it does
   what 0.2 did and **never** runs health checks or reboots: a committed system
   is not rolled back for being unwell.
6. On a trial boot it runs the health checks: every executable in
   `/usr/lib/pengyos/update-health.d/` and `/etc/pengyos/update-health.d/`,
   merged by file name in name order, with a file in `/etc` replacing the
   image's file of the same name (a symlink to `/dev/null` disables one).
   Each check gets 60 s. A check passes by exiting 0; any other exit code, a
   timeout or a failure to run it is a failure, and its last output line is
   the reason.
7. Failing checks are retried every 10 s until `health_timeout` (seconds,
   `update.conf`, default 180) has passed since the first attempt, so a
   check waiting for DHCP is not a failure.
8. If they all pass, the slot is committed (as in 0.2) and the trial record
   is cleared. If any still fails: the trial is marked failed (step 11), the
   login message says so, and the machine reboots (`systemctl reboot`). The
   committed slot is still the old one and GRUB has already cleared
   `pengyos_try`, so that boot is the old system.
9. `confirm` without `--boot` (the command `apply`'s refusal tells the user
   to run) commits the running slot without health checks: the user is
   choosing to keep it.
10. The image ships one check, `10-critical-units`: `nftables.service`,
    `sshd.service` and `systemd-networkd.service` must each be active if it
    is enabled. A box whose firewall did not load, or that cannot be reached,
    is not kept.

### Failed versions are remembered
11. A trial is failed when either (a) its own health check failed (step 8, with
    that reason), or (b) a later boot runs `confirm --boot` on the *other* slot
    while a trial is recorded and grubenv `pengyos_try` is empty (GRUB
    consumed it and the trial never confirmed; reason "did not finish
    starting"). The version moves into `failed[<version>] = {at, reason}` and
    the trial record is removed. Both are idempotent.
12. `apply` without `--version` refuses a failed version with a message naming
    the date and reason, and how to retry (`--version`). It does not fall back
    to an older release. `apply --version X` clears X's failure when X
    installs. `check --save` puts the failure in the login message instead of
    "available".
13. `auto-run` never installs a failed version. A newer release is eligible
    again (failures are per exact version).

### A trial boot always ends
14. `render_grub_cfg` ends each kernel line with `${pengyos_serial}
    ${pengyos_trial}`. Inside the `pengyos_try` branch GRUB sets
    `pengyos_trial="pengyos.trial=1"`; elsewhere it is empty. Kernel arguments read back
    from an existing `grub.cfg` drop both variables before re-rendering
    (round trip stable). An update from an older updater boots its first trial
    with the old template (no marker), because `apply` runs the old system's
    renderer; that trial's `confirm` rewrites `grub.cfg`, and every trial
    after it has the marker. The marker is per boot, not per entry: picking
    the old slot by hand from the GRUB menu during a trial boots it with the
    marker too. That is harmless, because its `confirm --boot` writes the
    confirmation at once and an emergency there restarts at most once (the
    next boot has no `pengyos_try`).
15. Kernel panics reboot after 10 s (`CONFIG_PANIC_TIMEOUT=10`, Server layer).
16. systemd pets a hardware watchdog (`RuntimeWatchdogSec=60s`, Server layer,
    `iTCO_wdt`/`sp5100_tco` when the board has one; systemd 258 opens the
    device when it appears). A wedged PID 1 reboots the machine.
17. `pengyos-update-deadline.service` (`ConditionKernelCommandLine=pengyos.trial=1`,
    no default dependencies, started from `sysinit.target`) waits 15 minutes,
    then reboots unless `/run/pengyos-update/confirmed` exists (written by
    `confirm`). A trial that hangs before or during `multi-user.target`
    therefore falls back. `systemctl stop pengyos-update-deadline` cancels it
    on a trial being debugged at a console.
18. `emergency.service` and `rescue.service` get a drop-in that, on a trial
    boot only, logs the reason and reboots instead of waiting at a console.
    On any other boot they behave exactly as before.

### Unattended updates (opt-in)
19. `pengyos-update-auto.timer` is disabled by preset. `auto on` writes
    `auto = on`, `auto_at`, `auto_soak_days` to `/etc/pengyos/update.conf`,
    writes a timer drop-in with `OnCalendar=*-*-* HH:MM:00`, reloads systemd
    and enables and starts the timer. `auto off` writes `auto = off` and
    disables and stops it. The defaults are `--at 04:00` and `--soak 3`, and
    `--soak 0` is allowed. `auto` alone prints the setting, the
    channel, the next run and the last run's result. The timer has no
    random delay and is not `Persistent`, so a machine that was off at 04:00 does
    not reboot whenever it comes back.
20. `auto-run` does nothing unless `auto = on`. It skips (logging why) when
    the running boot is an unconfirmed trial or a trial is staged. It reads
    the feed, records the first time each version on the machine's channel was
    seen (`seen[<version>]`, pruned to versions still in the feed), and picks the
    newest version that is newer than the running one, not failed, and was first seen at least
    `auto_soak_days` days ago. It applies it exactly as `apply --version`
    would (same signature checks, hooks and refusals), and reboots **only** if
    that succeeded.
21. Every run records `auto = {at, result, version}` in the state file:
    `nothing-newer`, `waiting` (with the date the newest becomes eligible),
    `skipped`, `applied` or `error` (with the message). `status` and `auto`
    print it, and a failure or a rollback also goes in the login message.
22. The machine's channel is used as is. On `testing`, unattended updates
    mean nightly reboots; that is the administrator's choice, and `auto on`
    prints a warning.

## Acceptance criteria
- Unit tests cover:
  - trial recording;
  - health-check selection (merge, override, `/dev/null`) and its retry and timeout;
  - both ways a trial fails (step 11);
  - `apply`'s and `auto-run`'s refusal of failed versions;
  - the soak window;
  - `auto on/off` writing the config and the timer drop-in;
  - the GRUB trial marker and the kernel-argument round trip;
  - `confirm --boot` never rebooting a committed system.
- Repo tests check that:
  - the auto timer is preset-disabled and not enabled in the overlay;
  - the deadline and emergency units are conditioned on `pengyos.trial=1`;
  - `CONFIG_PANIC_TIMEOUT=10` is in the Server fragment;
  - the confirm unit runs `confirm --boot`.
- In QEMU (before the first unattended update on a real machine): a trial
  made to panic, to hang before `multi-user.target`, to enter emergency mode,
  and to fail a health check each come back to the old slot with no input and
  record the version as failed. The next `auto-run` does not reinstall it.

## Out of scope
- Mail, BotTalk or other notification: the operations layer can read
  `state.json` (`docs/operations.md`).
- Staged rollouts or server-side publish dates in the feed. The soak window
  is measured from the first sighting on this machine.
- Logged-in-user or inhibitor checks before an unattended reboot. The window
  is the policy.
