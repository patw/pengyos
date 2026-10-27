# pengyos-webui — a per-user on/off for the Pengy web UI

## Summary
A POSIX-shell tool shipped in the Server overlay that turns `pengy-web` on and
off for a named user, as a systemd template unit running **as that user**,
bound to loopback by default, surviving reboots, on Server and Desktop alike.

## Purpose
`pengy-web` is in the Server and Desktop images and has no authentication: it
exposes `run_bash` and `run_python`. Serving it is a decision with a named
consequence, so it gets a bounded command that states the consequence, keeps
the loopback default, and refuses to be accidental. Today the only way to run
it as yourself at boot is to hand-write a unit; the one unit in this repo
(the since-removed `tools/pengy-agent-package`) was written for PengyR and was wrong for this
image (`--config`, `DynamicUser`, `ProtectHome`, root).

## Interface
```
pengyos-webui status [--json]
sudo pengyos-webui on [USER] [--port N] [--host ADDR]
sudo pengyos-webui off [USER]
pengyos-webui --help
pengyos-webui --root DIR …        # test hook: write under DIR, no root needed
```
- `USER` defaults to `$SUDO_USER`; validated as a password-database user **and**
  as a legal systemd instance name (`%i`) before anything is written.
- `status` is read-only and works unprivileged. `--json` is accepted before or
  after the subcommand; human output is unchanged when it is absent.
- Misuse exits 2 with a message naming the misuse. `on` twice is a no-op
  rewrite; `off` when off is a no-op.

## Deliverable
| File | Ships at |
|---|---|
| `buildroot-external/board/server/rootfs-overlay/usr/bin/pengyos-webui` | `/usr/bin/pengyos-webui` (Server, Desktop) |
| `buildroot-external/board/server/rootfs-overlay/etc/systemd/system/pengy-web@.service` | `/etc/systemd/system/pengy-web@.service` |
| `tools/pengyos-webui/` | the test suite and its docs (not in the image) |

Runtime state, all under `/etc` (the Tier-1 overlay, untouched by updates):
`/etc/pengyos/webui.conf`, the per-user drop-in
`pengy-web@USER.service.d/pengyos-webui.conf`, and the enable symlink.

## Design decisions the tests enforce
1. **System template, not a user unit.** It must start at boot on a headless
   Server with no session and no login; a user unit needs
   `loginctl enable-linger` and only starts after the first login.
2. **`User=%i`, `WorkingDirectory=%h`, no `DynamicUser`, no `ProtectHome`.**
   The UI is the agent as that person: it needs `~/.config/pengy`, `~/skills`,
   `~/.secrets`.
3. **Loopback is the only default.** `--host` is explicit, documented as
   unauthenticated, surfaced in `status` (human and JSON), and the unit's own
   `Environment=WEBUI_HOST=127.0.0.1` keeps the default true for a unit
   enabled by hand.
4. **One port is one process.** A second user on a held port is refused with a
   message, not written down as a unit that restarts in a loop.
5. **Nothing is written under `/usr`** at runtime: it is a read-only signed
   image.

## Verification (tests are first-class)
`python3 -m pytest -q` in this directory, no root / systemd / build / network:

- the script is `#!/bin/sh`, executable in git, and passes `sh -n` and `dash -n`;
- the unit is asserted **byte for byte**, and structurally: `User=%i`,
  `WorkingDirectory=%h`, `Restart=on-failure`, `WantedBy=multi-user.target`,
  no `DynamicUser`, no `ProtectHome`, no `User=root`, loopback default;
- `on` / `off` / `status` run against `--root DIR` with `tests/fake-systemctl`;
- state file, drop-in text, enable symlink, and the exact `systemctl` call
  sequence are asserted;
- idempotency, port collision, validation of user/port/host, the root guard on
  the real path, unprivileged `status`, `--json` in both positions;
- Core ships neither file, Desktop ships no second copy, and the old PengyR unit is
  gone.

## Acceptance criteria
- `pytest -q` green here; `pengyos_gate.py run --mode fast` green.
- On a real Server image: `sudo pengyos-webui on` → `curl -s localhost:5000`
  answers; reboot; it answers again without a login; `sudo pengyos-webui off`
  stops it and it stays stopped across a reboot.

## Non-goals
- A GUI. It is a shell tool because Server has no COSMIC and no session.
- Authentication, TLS, a reverse proxy, `--trusted-host`, remote access.
- Publishing the UI on a non-loopback address as a supported configuration.
- Core: no `pengy-web`, so no tool and no unit.

## Constraints
Runs as root for `on`/`off` (the state is system-wide), unprivileged for
`status`. Tests run as an ordinary user against a sandbox root. The tool
writes only under `/etc`.
