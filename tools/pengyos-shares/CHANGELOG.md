# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.0] - 2026-10-30

### Added
- `pengyos-shares`, shipped in the Server overlay (`board/server/rootfs-overlay/
  usr/bin/`), so the Server and the Desktop have it: `status [--json]`, `add`,
  `remove`, `passwd [--stdin]`, `deluser`. Changes need root; `status` does not.
  Misuse exits 2 and writes nothing.
- The server is the kernel's ksmbd (`CONFIG_SMB_SERVER=m`) with ksmbd-tools;
  no Samba server is built. `ksmbd.service` is preset off and enabled with the
  first share; port 445 is opened to LAN ranges only through pengyos-firewall,
  and both are undone with the last share.
- Share passwords are NT hashes in `/etc/ksmbd/ksmbdpwd.db` (0600), computed by
  the tool (a pure-Python MD4: OpenSSL's legacy provider is often not loaded),
  so a password is never on a command line.
