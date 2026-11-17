# pengyos-alert

Send an alert from a PengyOS machine to a person: a push over
[ntfy](https://ntfy.sh) and/or a mail over SMTP, and always a journal line.
Shipped in the Server overlay (Server and Desktop), stdlib-only Python.

```sh
sudoedit /etc/pengyos/alert.conf        # NTFY_URL=, or MAIL_TO= + SMTP_HOST=
sudo install -m 600 /dev/null /etc/pengyos/alert.cred   # NTFY_TOKEN= / SMTP_PASSWORD=, if needed
sudo pengyos-alert test                 # does it arrive?
pengyos-alert status
```

What already calls it: smartd (a failing or hot disk), `pengyos-backup`,
`pengyos-monitor`, `pengyos-update-check` and `pengyos-update-auto` failing, and a
filesystem past `DISK_ALERT_PCT` in `/etc/pengyos/monitor.conf`. Your own units
join with one line in `[Unit]`:

```ini
OnFailure=pengyos-alert@%n.service
```

and scripts with `sudo pengyos-alert send --key my-check "what happened"`. The
same `--key` is sent at most once per `REPEAT_HOURS` (12 by default).

The contract is [spec.md](spec.md). Tests: `python3 -m pytest -q`.
