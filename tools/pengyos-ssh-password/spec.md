# pengyos-ssh-password: spec

## Why

PengyOS Server shipped key-only SSH, the right default. In the field (2026-11-07),
people new to Linux and Windows admins could not get in: they had never made an SSH
key, did not know where one lives, and the installer's key import (GitHub user, URL,
user@host) assumed they had one. The fix keeps keys as the default and makes
password logins an explicit, visible, reversible choice.

## Contract

1. Default image: `PasswordAuthentication no`, root `prohibit-password`. Unchanged.
2. `on`, as root: write the drop-in (`PasswordAuthentication yes`,
   `PerSourcePenalties authfail:15s`) and the motd reminder. Unless `--offline`:
   `sshd -t` must pass, otherwise the drop-in is removed (if `on` created it) and the
   exit is 6. Reload sshd if it is active.
3. `off`, as root: when no account (uid 1000-59999, with a login shell) has a key in
   `~/.ssh/authorized_keys`, refuse with exit 3 and print how to add one, unless
   `--force`. Otherwise remove both files and reload sshd. `off` while off is a no-op.
4. `status` (any user): on or off; each account's key count (`?` where the file is
   unreadable); and while on, the steps to make and copy a key, with this machine's
   name filled in.
5. No mode of the tool lets root log in with a password.
6. The installer (`pengyos-install`, `ProvisionAnswers.ssh_password`) turns it on with
   `chroot <target> pengyos-ssh-password on --offline`, so both write the same files.

## Acceptance

- The unit suite (`tests/`) covers 2-5 against a sandbox root.
- On a booted Server image (QEMU): `on`, then a password login as the user works and
  as root is refused; `off`, then the password login is refused and a key login works.
