# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this tool keeps its own semver (only images use the repo-root `VERSION`).

## [0.1.1] - 2026-10-27

### Changed
- `on --host ADDR` now names the command that opens the port on a Server,
  `sudo pengyos-firewall allow PORT --from CIDR`, instead of saying to edit
  `/etc/nftables.conf`. An edited copy of that file shadows every later image's
  ruleset.

## [0.1.0] - 2026-10-25

### Added
- `pengyos-webui`, shipped in the Server overlay (`board/server/rootfs-overlay/
  usr/bin/`), so Server and Desktop get one copy and Core gets none: Core has
  no `pengy-web`. `status` / `on [USER] [--port N] [--host ADDR]` / `off
  [USER]`, `--help`, `--json` accepted before or after the subcommand, misuse
  exits 2. `on`/`off` need root; `status` works unprivileged.
- `pengy-web@.service`, the system template (`%i` = the user): `User=%i`,
  `WorkingDirectory=%h`, `Restart=on-failure`,
  `WantedBy=multi-user.target`, `Environment=WEBUI_PORT=5000` /
  `WEBUI_HOST=127.0.0.1` as the documented defaults, and deliberately **no**
  `DynamicUser` and **no** `ProtectHome` — the agent has to see the invoking
  user's `~/.config/pengy`, `~/skills` and `~/.secrets`. A system unit, not a
  user unit, so it comes up at boot on a headless Server with no session and
  no `loginctl enable-linger`.
- State in `/etc/pengyos/webui.conf` (one `user=NAME PORT HOST` line per
  enabled user) plus the per-user drop-in that carries the port and bind to
  the service. All under `/etc`: the overlay that survives OS updates.
- The loopback default is the only default. `--host` is explicit, documented
  as unauthenticated, and surfaced by `status` in both outputs. A second user
  on a port someone already holds is refused.
- `--root DIR`, the test hook: every write goes under `DIR` and the root guard
  is lifted for that run, because nothing outside `DIR` is touched.
- `tests/test_pengyos_webui.py` + `tests/fake-systemctl`: the unit asserted
  byte for byte, the state file, the generated drop-in, the `systemctl` call
  sequence, idempotency, the collision rule, validation of user/port/host, the
  root guard on the real path, unprivileged `status`, `--json` in both
  positions, and which boards ship what. No root, no systemd, no build.
- `spec.md`, `README.md`, `verify.toml`, MIT `LICENSE`.

### Removed
- `tools/pengy-agent-package/package/pengy/pengy-web.service`, and the
  `pengy-agent-package` tests that asserted its shape. It was written for
  PengyR: `--config /etc/pengy/settings.json` (this binary takes
  `--config-dir`), `DynamicUser=yes` and `ProtectHome=yes` (which hide
  `~/.config/pengy`, `~/skills` and `~/.secrets` — all the agent's state), and
  a root-run `multi-user.target` service. The live recipe
  (`buildroot-external/package/pengy/pengy.mk`) never installed it, so it was
  a unit no image shipped, describing a service no image ran. Replaced by
  `pengy-web@.service`; the tool's `README.md`/`spec.md` and CHANGELOG stop
  promising a unit the recipe does not install.
