# pengyos-ssh-password

Let SSH accept passwords as well as keys, or go back to keys only (the default).

```sh
pengyos-ssh-password                     # on or off, and how many keys each account has
pengyos-ssh-password status --json
sudo pengyos-ssh-password on             # keys or password (less safe; root stays keys-only)
sudo pengyos-ssh-password off            # keys only again (refuses while no account has a key)
sudo pengyos-ssh-password off --force    # keys only even so: SSH then works for nobody
```

PengyOS's sshd accepts keys only, and that stays the default. It is the safer
setting, but someone who has never made an SSH key cannot get in at all. The
installer asks the question ("Keys only" or "Keys or password"), and this command
changes the answer later. While passwords are on, the usual way to get a key
there becomes easy: log in once with the password, or run `ssh-copy-id` (Windows:
the PowerShell line that `status` prints). Then turn passwords off.

## What it changes

- `on` writes `/etc/ssh/sshd_config.d/50-pengyos-ssh-password.conf`:
  `PasswordAuthentication yes`, plus `PerSourcePenalties authfail:15s`, so an
  address that keeps failing waits longer between tries (OpenSSH's default is 5 s).
  It runs `sshd -t` and removes the file again if sshd refuses it (exit 6), then
  `systemctl reload sshd`. It also writes `/etc/motd.d/50-pengyos-ssh-password`,
  a two-line reminder that pam_motd prints at every login.
- `off` removes both files and reloads sshd. Open sessions stay open.
- root never logs in with a password over SSH: `PermitRootLogin prohibit-password`
  stays in the image's `sshd_config`, and the drop-in does not touch it.

The image's `/etc/ssh/sshd_config` begins with `Include /etc/ssh/sshd_config.d/*.conf`.
sshd keeps the first value it reads for a setting, so the drop-in wins over the
image's `PasswordAuthentication no` without editing that file. `/etc` is the data
overlay, so the drop-in survives updates while `sshd_config` keeps coming from the image.

Exit codes: 0 ok; 2 usage; 3 `off` refused (no account has a key); 5 not root;
6 sshd rejected the configuration (nothing changed).

## Tests

`python3 -m pytest -q` runs the shipped script against a sandbox root with fake
`sshd` and `systemctl`. It also checks the image's wiring: the `Include` comes before
`PasswordAuthentication no`, sshd.service has `ExecReload`, and features-required
lists the tool.
