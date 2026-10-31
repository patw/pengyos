# pengyos-remote

Let another computer see and use this desktop over RDP, from the shell. The
Remote Desktop window (`pengyos-remote-desktop`) is buttons over these same
commands.

```sh
pengyos-remote password              # the RDP password (not your login password)
pengyos-remote on [--port 3389]      # share this desktop while you are logged in
pengyos-remote on --local            # 127.0.0.1 only: reach it through an SSH tunnel
pengyos-remote status [--json]       # on or off, where to connect, the cert fingerprint
pengyos-remote off
pengyos-remote forget                # ask again on screen before the next share
```

It shares the session you are logged into: what is on the screen, with your
mouse and keyboard, not a second session. The server (`pengyos-rdp-server`,
tools/pengyos-rdp-server) runs as you in that session, as the user unit
`pengyos-rdp-server.service`; `on` enables it, so it starts at every desktop
login, and it cannot run without one.

Clients: Remote Desktop Connection (Windows), Windows App (macOS, iOS),
Remmina or `xfreerdp3` (Linux). Log in with your user name and the RDP
password. The first connection asks on this screen whether to allow it
(the desktop portal); the answer is kept until `forget`.

## Security

- Every connection logs in first (NLA / CredSSP) with the RDP password, which
  is a separate secret: `~/.config/pengyos-remote/password`, 0600. The server
  refuses to start if that file is readable by anyone else.
- TLS with a certificate made for this machine on first `on`
  (`cert.pem`/`key.pem`, RSA 3072, 10 years). Clients ask you to trust it once:
  compare the SHA-256 `status` prints.
- The Desktop's firewall already allows ports above 1024, so 3389 is reachable
  from the LAN while it is on. `--local` keeps it on 127.0.0.1 for SSH-only use.
- Nothing needs root: everything lives in your home and your user manager.

## Files

`~/.config/pengyos-remote/` (0700): `password`, `cert.pem`, `key.pem`, `env`
(the listen address). `~/.local/state/pengyos-remote/`: `restore-token` (the
saved permission) and `server.log`.
