# pengyos-remote — spec

## Contract

- `board/desktop/rootfs-overlay/usr/bin/pengyos-remote`, stdlib Python, run as
  the user (root is refused). Misuse exits 2 and changes nothing.
- `password [--stdin]`: 8–128 characters, no line breaks; written atomically
  0600 in a 0700 directory. A running server is restarted to take it.
- `on [--port N] [--local]`: refuses without a password and for ports ≤ 1024.
  Makes the certificate once (`openssl req -x509 -newkey rsa:3072`, CN and SAN =
  the host name) and keeps it after that (clients trusted it). Writes
  `env` = `RDP_ADDR=0.0.0.0:N` (or `127.0.0.1:N`), enables and restarts
  `pengyos-rdp-server.service`. Outside a desktop session the start fails
  (`Requisite=graphical-session.target`) and `on` says it starts at login.
- `status [--json]`: enabled, state, user, port, local_only, host, LAN
  addresses (container and VM bridges left out), password_set, fingerprint,
  consent_saved, the log's last lines.
- `off`: `disable --now`. `forget`: delete the portal restore token.
- The unit: `ExecStart=/usr/libexec/pengyos-rdp-server`,
  `EnvironmentFile=-%h/.config/pengyos-remote/env`, output appended to
  `~/.local/state/pengyos-remote/server.log` (a user cannot read their own
  journal when journald keeps it in memory), `Restart=on-failure`.

## Verification

- `tools/pengyos-remote/tests` (15 tests): fake systemctl, openssl, ip.
- 2026-10-31 on behoweather (PengyOS Desktop, live COSMIC session): with the
  tool, unit and server copied into the home directory, `password --stdin` and
  `on --local` started the server (portal stream 1920x1080, listening on
  127.0.0.1:3389). Through `ssh -L`, FreeRDP 3.32 logged in and showed the
  machine's real screen; a wrong password was refused at NLA (`LogonDenied`).
  `off` closed the port.
