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
- `approve [--output NAME]`: refuses while off (exit 2). The display is the one
  connected DRM connector (`/sys/class/drm/card*-*/status`), or `--output`,
  which must be connected; none connected exits 1, several without `--output`
  exit 2. Writes, through `busctl --user` to
  `org.freedesktop.impl.portal.PermissionStore`, `Set("remote-desktop", true,
  UUID, {"": ["yes"]}, ("COSMIC", 1, (3, false, true, [NAME], [])))`, the grant
  the portal stores for Allow; deletes the previous token's entry; writes the
  UUID to `restore-token` (0600). A running server is restarted and must log
  `rdp: listening` within 25 s, else exit 1; a stopped one takes it at login.
- `status` reports `waiting_for_approval`: the log's last `portal: starting`
  comes after its last `rdp: listening` / `portal: stream node`.
- The unit: `ExecStart=/usr/libexec/pengyos-rdp-server`,
  `EnvironmentFile=-%h/.config/pengyos-remote/env`, output appended to
  `~/.local/state/pengyos-remote/server.log` (a user cannot read their own
  journal when journald keeps it in memory), `Restart=on-failure`.

## Verification

- `tools/pengyos-remote/tests` (22 tests): fake systemctl, openssl, ip, busctl
  and `/sys/class/drm`.
- 2026-11-11 on behobasement (Desktop 26.11.13, headless on its virtual
  display, the server waiting on the on-screen question since boot): `approve`
  over SSH had it listening and streaming 1920x1080 in 0.4 s; a cold reboot
  then came up listening with no one at the machine.
- 2026-10-31 on behoweather (PengyOS Desktop, live COSMIC session): with the
  tool, unit and server copied into the home directory, `password --stdin` and
  `on --local` started the server (portal stream 1920x1080, listening on
  127.0.0.1:3389). Through `ssh -L`, FreeRDP 3.32 logged in and showed the
  machine's real screen; a wrong password was refused at NLA (`LogonDenied`).
  `off` closed the port.
