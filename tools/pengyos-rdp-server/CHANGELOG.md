# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.0] - 2026-10-31

### Added
- The RDP server, from the 2026-10-21..23 prototype (pengyos-remote-proto): the
  portal + PipeWire + ironrdp path, cursor as an RDP pointer, keyboard (scancodes
  and Unicode), mouse and wheel, takeover by a new client.
- Configuration from `~/.config/pengyos-remote` instead of environment
  variables; the RDP user is the login name; a password file others can read is
  refused.
