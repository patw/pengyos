# pengyos-alert — tell a person that something on this machine needs them

## Summary
A stdlib-only Python script shipped in the Server overlay (so the Desktop has it
too) that sends one alert over ntfy and/or SMTP mail, and always writes it to the
journal. smartd, the Server's own timers' services (through `OnFailure=`) and
the metrics timer's disk check call it; anything else can.

## Purpose
US-SRV-3 ("a deliberately induced failure produces an alert") and US-SRV-4
("backup failures raise an alert") were not implementable: PengyOS has no mail
transport, so smartd's `-m` mail could never go out, and a failed backup or a
full disk was a journal line nobody read on a headless box. A mail daemon
(msmtp, postfix) would add a GPL package and a queue for what is one HTTP POST or
one SMTP session; Python's `urllib` and `smtplib` are already in the image.

## Interface
```
pengyos-alert status [--json]
sudo pengyos-alert test
sudo pengyos-alert send [--title T] [--priority low|default|high|urgent] [--key K] MESSAGE...|-
pengyos-alert unit-failed UNIT        # pengyos-alert@UNIT.service
pengyos-alert smartd [ADDRESS...]     # smartd's -M exec hook; reads SMARTD_*
pengyos-alert … --root DIR            # test hook: files under DIR, fakes from DIR/usr/bin
```
- `status` works unprivileged and never prints a secret.
- Every other command needs root (the secrets file and the state are root's).
- Exit: 0 sent, or journal-only because no transport is configured; 1 every
  configured transport failed (or `test` with nothing configured); 2 misuse.

## Configuration
`/etc/pengyos/alert.conf` (0644, shipped with every line commented out):
`NTFY_URL`, `MAIL_TO`, `MAIL_FROM` (default `pengyos-alert@<hostname>`),
`SMTP_HOST`, `SMTP_PORT` (default by mode), `SMTP_TLS` (`starttls` 587 default,
`tls` 465, `none` 25), `SMTP_USER`, `REPEAT_HOURS` (default 12).
`/etc/pengyos/alert.cred` (root, 0600): `NTFY_TOKEN`, `SMTP_PASSWORD`. A secret
written in alert.conf is ignored with a warning. An alert.cred readable by group
or others is not used, with a warning (as ssh treats a readable key).
State: `/var/lib/pengyos-alert/sent.json` (`key -> last sent`, and `last_sent`).

## Behaviour
1. The alert goes to the journal first (`logger -t pengyos-alert`, syslog
   level by priority: info, notice, warning, crit).
2. Then to each configured transport, 15 s timeout each. ntfy: POST the body
   to `NTFY_URL` with `Title`, `Priority` (2-5), `Tags`, and
   `Authorization: Bearer` when a token is set; non-latin-1 titles are RFC 2047
   encoded. Mail: one message, `X-Priority` for high/urgent.
3. With `--key`, a key sent less than `REPEAT_HOURS` ago is not sent again
   (journaled as suppressed, exit 0). Only a successful send starts the window.
   `test` and keyless `send` are never suppressed.
4. Bodies over 3500 characters are cut (ntfy's limit is 4096 bytes).
5. `unit-failed UNIT`: title `<host>: UNIT failed`, priority high, key
   `unit:UNIT`, body `systemctl status --full --lines=25` plus the journalctl
   line to read.
6. `smartd`: device from `SMARTD_DEVICESTRING`/`SMARTD_DEVICE`, text from
   `SMARTD_FULLMESSAGE`/`SMARTD_MESSAGE`, key `smart:<device>:<failtype>`;
   priority urgent for `Health`, default (never suppressed) for `EmailTest`,
   high otherwise.

## Wiring (Server overlay)
| File | Does |
|---|---|
| `usr/bin/pengyos-alert` | the tool |
| `usr/lib/systemd/system/pengyos-alert@.service` | `unit-failed %i`, root, sandboxed; no `OnFailure=` of its own |
| `etc/systemd/system/pengyos-{backup,monitor,update-check}.service`, `usr/lib/systemd/system/pengyos-update-auto.service` | `OnFailure=pengyos-alert@%n.service` |
| `etc/smartd.conf` | `DEVICESCAN -a -m root -M exec /usr/libexec/pengyos/pengyos-alert-smartd` |
| `usr/libexec/pengyos/pengyos-alert-smartd` | `exec pengyos-alert smartd` |
| `usr/libexec/pengyos/pengyos-monitor.sh` + `etc/pengyos/monitor.conf` | `DISK_ALERT_PCT=90`: a filesystem at or above it sends `--key disk:<mount>` |
| `etc/pengyos/alert.conf` | the documented, inactive config |

## Acceptance
- With nothing configured, every caller still succeeds and the journal has the
  alert; `status` says "none configured".
- With `NTFY_URL` set, `sudo pengyos-alert test` arrives on the subscribed phone.
- `sudo systemd-run --wait -p OnFailure=pengyos-alert@induced-failure.service false`
  (a deliberately induced failure, US-SRV-3) sends `<host>: induced-failure failed`.
- A failing `pengyos-backup.service` alerts once, not on every retry, within
  `REPEAT_HOURS` (US-SRV-4).
- `-M test` added to smartd.conf plus `systemctl restart smartd` sends one test.

## Verification
`python3 -m pytest -q` (verify.toml): the shipped script against a sandbox root,
real ntfy/SMTP over loopback, the unit wiring, and pengyos-monitor.sh with a
fake `df`. On a booted Server image: the three acceptance commands above.
