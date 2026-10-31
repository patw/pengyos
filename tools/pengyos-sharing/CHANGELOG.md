# Changelog — pengyos-sharing

## 0.1.0 — 2026-10-30

- First version: the File Sharing window over `pengyos-shares`. See what is
  shared and the addresses other computers use (`\\host\name`,
  `smb://host/name`), share a folder (a folder picker, a suggested name,
  read-only or not), change or stop sharing one, and set your share password.
  Changes run `pkexec /usr/bin/pengyos-shares …`; administrators (wheel) need no
  password (`60-pengyos-shares.rules`).
