# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.2.0] - 2026-11-11

### Added
- `approve [--output NAME]`: allow sharing without the on-screen question, for a
  machine nobody sits at. The portal keeps an Allow as a one-use token renewed at
  each start, so an interrupted start (a display change, a restart mid-way)
  spends it and the next start asks on a screen nobody watches: behobasement
  (headless, its virtual display) stayed down after 26.11.13. `approve` writes
  the grant the portal itself stores for Allow (`remote-desktop` table, app id
  "", `("COSMIC", 1, (3, false, true, [OUTPUT], []))`) under a new UUID token,
  deletes the spent one, restarts the server and waits for it to listen; it fails
  loudly if the portal still asks. One connected display is picked by itself;
  with several, `--output` names it.
- `status` says when the server waits on the on-screen question
  (`waiting_for_approval` in `--json`) and points at `approve`.

### Verified
- behobasement (Desktop 26.11.13, no monitor, waiting since boot): `approve` over
  SSH had the server streaming 1920x1080 and listening in 0.4 s; after a cold
  reboot it came up listening with nobody touching it.

## [0.1.0] - 2026-10-31

### Added
- `pengyos-remote` (Desktop overlay): `status`, `password`, `on [--port]
  [--local]`, `off`, `forget`. Runs `pengyos-rdp-server` as a user unit in the
  desktop session; the password, certificate and settings are the user's own.
