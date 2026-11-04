# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.0] - 2026-11-03

### Added
- `pengyos-services status [NAME] [--all] [--json] [--check] [--probe-from HOST]`,
  shipped in the Core overlay (`board/core/rootfs-overlay/usr/bin/`), so every
  edition has it. Read-only. Covers systemd units (system and user), Quadlets (the
  system and the user lane) and `pengyos-vm` VMs.
- Autostart from what systemd will do, not `is-enabled`: `yes`, `at-login-only`
  (a user service without linger), `on-demand (X.socket)`, `via-timer (X.timer,
  next run …)`, `no`, each with `why`.
- Ports from `ss`, mapped to units through `/proc/PID/cgroup` (and, for a `podman
  run` inside a unit, through its conmon), plus the ports Quadlets and VMs declare.
  Each with bind, firewall verdict and LAN reachability, `by rules` or `tested`.
  A rootful Quadlet's publish is reported as DNAT, which the input rules don't filter.
- Memory as `memory.peak` and `anon`, with page cache (`file`) apart.
- Problems with the command that fixes each: `failed`, `quadlet-no-unit` (quoting
  the generator, naming the key), `user-no-linger`, `port-collision`,
  `served-not-reachable` (warning), `loopback-only-forward`, `open-not-served`
  (warning). `--check` exits 1 on an error-level problem.
- Written for Pengy's Server field review (2026-11-03): findings F2, F4, F5, F11.
