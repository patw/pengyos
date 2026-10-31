# pengyos-sharing — "File Sharing"

Share folders with the other computers on your home or office network, from the
PengyOS Desktop. Windows opens them at `\\computer\name`, a Mac or Linux machine
at `smb://computer/name`. Nothing is reachable from the internet.

- **Sharing is on/off**: on by itself while at least one folder is shared.
- **Shared folders**: each with its folder, read-only or read-and-write, who may
  use it, and both addresses, with **Change…** and **Stop sharing** (which
  leaves the folder and its files alone).
- **Share a folder…**: pick a folder; the name on the network is suggested from
  it; read-only is a switch.
- **Your share password**: what other computers log in with, together with your
  user name. It is not your login password, and until it is set nobody can
  open your shares (the app says so).

## How it works

The app is a window over `pengyos-shares` (board/server overlay, also on the
Desktop), which does the work as root: ksmbd's configuration, the share
passwords, starting and stopping ksmbd, and port 445 opened to the private LAN
ranges only through `pengyos-firewall`. Reading uses `pengyos-shares status
--json`; every change is `pkexec /usr/bin/pengyos-shares …`, the password on
stdin. Administrators (wheel) at the computer run that one program without a
password (`/etc/polkit-1/rules.d/60-pengyos-shares.rules`, the way they manage
printers and updates); anyone else is told only administrators can share.

- `src/lib.rs` — std + serde_json: the status model, the checks (the CLI's
  name rules, system folders, password length), suggested names, the command
  lines and how failures are worded.
- `src/app.rs` (feature `cosmic`) — the window.

A share made in the app is for you; changing one keeps who may use it. Shares
for several people: `sudo pengyos-shares add NAME PATH --user a --user b`.

## Use

```console
pengyos-sharing          # the window
pengyos-sharing --list   # what is shared, as text
```

## Tested

`cargo test` covers the logic without a display or root.
