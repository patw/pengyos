# pengyos-webui

Turn the **Pengy web UI** on and off for a user, from the shell, on Server and
Desktop alike.

```sh
pengyos-webui status [--json]        # what is running, on what URL
sudo pengyos-webui on [USER]         # serve it as USER on http://127.0.0.1:5000
sudo pengyos-webui off [USER]        # stop it and take it out of the boot
```

`pengy-web` ships in the Server and Desktop images already, and it has **no
login**: it exposes `run_bash` and `run_python`, so whoever can reach the port
chats with the agent, reads and writes the files that user can, and runs
commands as that user. This tool exists so the choice is a command with a
documented consequence, not a hand-written systemd unit.

The default bind is `127.0.0.1` — this machine only. `--host ADDR` is the
unauthenticated network bind, it is never a default, it has to be typed on
purpose, and `status` prints it every time.

## What it writes

| Path | What |
|---|---|
| `/etc/pengyos/webui.conf` | the state: one `user=NAME PORT HOST` line per enabled user |
| `/etc/systemd/system/pengy-web@USER.service.d/pengyos-webui.conf` | the per-user port and bind, handed to the service |
| `/etc/systemd/system/multi-user.target.wants/pengy-web@USER.service` | the enable symlink (`systemctl enable`) |

All three are under `/etc`, which on PengyOS is the overlay on the `data`
partition: it survives OS updates. Nothing is written under `/usr`, which is
the read-only signed image.

## The service

`pengy-web@.service` is a **system template** (`%i` = the user name):

```
User=%i
WorkingDirectory=%h
ExecStart=/usr/bin/pengy-web --port ${WEBUI_PORT} --host ${WEBUI_HOST}
Restart=on-failure
WantedBy=multi-user.target
```

It runs as the human, in their home, and sees their `~/.config/pengy`,
`~/skills` and `~/.secrets` — that is the point of the UI. So **no
`DynamicUser`** (there is no ephemeral user, the user is the subject) and
**no `ProtectHome`** (it would hide exactly the state the agent needs).

A system unit, not a user unit, because it has to come up at boot on a
headless Server where nobody has logged in and no session exists. A user unit
would need `loginctl enable-linger USER`, one more thing to explain, and it
would not start before the first login.

`--config-dir` is not passed: `pengy-web` defaults to `~/.config/pengy`, and
with `User=%i` systemd sets `HOME` from the password entry, so the default is
already the right directory.

## Where it ships

`buildroot-external/board/server/rootfs-overlay/` — the Server layer, which
the Desktop image stacks under its own, so one copy serves both. Core ships
neither the tool nor the unit: Core has no `pengy-web`.

## Testing

```sh
cd tools/pengyos-webui && python3 -m pytest -q
```

The suite drives the real script with `--root DIR`, a sandbox root: every
write goes under `DIR` and the root guard is lifted for that run, because
nothing outside `DIR` is touched. `tests/fake-systemctl` answers
`enable/disable/restart/is-active/is-enabled` from a symlink and a marker
file, and logs every call. No root, no systemd, no build, no network.

It asserts the unit text byte for byte, the state file, the generated drop-in,
idempotency (`on` twice, `off` when off), the port-collision rule, the
validation of user names / ports / bind addresses, the root guard on the real
path, and that `status` works unprivileged.

## Limits

- One port is one process: a second user needs `--port`. The tool refuses a
  collision instead of writing a unit that would restart in a loop.
- `on` restarts the service, so a running chat session is dropped.
- The UI has no authentication. Loopback is the only safe default; a
  non-loopback bind is a decision, and on Server the firewall has to open the
  port as well.

MIT — Copyright (c) 2026 Pat Wendorf. See [`spec.md`](spec.md).
