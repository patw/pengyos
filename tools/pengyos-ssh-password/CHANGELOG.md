# Changelog

## 0.1.0 — 2026-11-07

- First release. `on` lets SSH accept people's passwords as well as keys
  (`/etc/ssh/sshd_config.d/50-pengyos-ssh-password.conf`, checked with `sshd -t`,
  then sshd reloaded) and adds a reminder to every login (`/etc/motd.d/`). `off`
  goes back to keys only, and refuses while no account has a key (`--force`
  overrides). `status` lists each account's key count and, while passwords are
  on, how to make a key and copy it over (`ssh-copy-id`, or the Windows
  PowerShell one-liner). root never logs in with a password over SSH.
  `on --offline` writes the files only: both installers' password choice runs
  it in the new system.
