# pengyos-shares

Share this machine's folders on the network over SMB, from the shell, on the
Server and the Desktop.

```sh
pengyos-shares status [--json]                         # what is shared, with whom
sudo pengyos-shares add media /data/media              # share a folder (for you)
sudo pengyos-shares add music /home/pat/Music --read-only --user pat --user sam
sudo pengyos-shares passwd pat                         # pat's share password
sudo pengyos-shares remove media                       # stop sharing (files untouched)
sudo pengyos-shares deluser sam                        # sam can no longer connect
```

Then, from another machine: `\\lidserver\media` in Windows' File Explorer, or
`smb://lidserver/media` in macOS Finder or a Linux file manager.

## How it works

The server is the kernel's own SMB3 server, **ksmbd**, with the ksmbd-tools
daemon (`ksmbd.service`). PengyOS ships no Samba server: SMB 2.1 and 3 are what
Windows 10/11, macOS, Linux and phones speak, and the Server image then builds
no Samba at all. (The Desktop has Samba's client library, for Files' `smb://`.)

- **Nothing listens until there is a share.** `ksmbd.service` is preset off. The
  first `add` enables and starts it and opens 445/tcp through
  `pengyos-firewall`; removing the last share stops and disables it and closes
  the port again.
- **LAN only.** Port 445 is opened from private and link-local addresses only
  (10/8, 172.16/12, 192.168/16, fd00::/8, fe80::/10), never the internet. To
  reach shares over a VPN, open its range by hand:
  `sudo pengyos-firewall allow 445 --from 100.64.0.0/10`.
- **Logins only.** No guest shares, no anonymous listing. A share user is an
  account on this machine (uid 1000 and up): files written through the share
  are owned by that account. Each user has a separate **share password**
  (`passwd`), stored as an NT hash in `/etc/ksmbd/ksmbdpwd.db` (0600). It is
  not the login password, and changing one does not change the other.
- `/etc/ksmbd/ksmbd.conf` is written by the tool (it says so in its first line).
  Keys you add to a share by hand are kept when the tool rewrites it; a
  `ksmbd.conf` it did not write is left alone, and `add` refuses until it is
  moved aside. `/etc` survives OS updates, so shares and passwords do too.
- `add NAME` again changes the share (path, users, read-only); `--user`
  defaults to the account that ran `sudo`. `passwd --stdin` reads the password
  from one line of standard input, for scripts and agents.

## Tests

```sh
cd tools/pengyos-shares && python3 -m pytest -q
```

The suite runs the shipped script against a sandbox root (`--root DIR`) with a
fake `systemctl`, and the real `pengyos-firewall` with its fake `nft`. It needs
no root and no ksmbd. That ksmbd serves the shares is proven by booting the
Server image: see `spec.md`.
