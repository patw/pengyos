# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.0] - 2026-10-31

### Added
- `pengyos-remote` (Desktop overlay): `status`, `password`, `on [--port]
  [--local]`, `off`, `forget`. Runs `pengyos-rdp-server` as a user unit in the
  desktop session; the password, certificate and settings are the user's own.
