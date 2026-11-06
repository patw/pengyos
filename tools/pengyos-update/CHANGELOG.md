# Changelog — pengyos-update

## [0.3.0] — 2026-11-06

### Added
- `spec.md`, the tool's contract (it had none).
- Health-gated confirm: `confirm --boot` (the boot unit) keeps a trial boot
  only if `/usr/lib/pengyos/update-health.d/` + `/etc/pengyos/update-health.d/`
  pass (retried for `health_timeout`, default 180 s); otherwise it records the
  version as failed and restarts into the old slot. A committed system is
  never checked or restarted. Until now a trial whose firewall failed to load
  was kept as soon as it reached multi-user.
- Failed versions are remembered (`failed` in `state.json`): from a failed check, or found
  by the old slot's boot when a recorded trial (`trial`, written by `apply`)
  was booted and never confirmed. `apply` refuses one unless named with
  `--version`; `check` and the login message say so.
- `auto` / `auto on [--at] [--soak]` / `auto off` / `auto-run`: opt-in
  unattended updates in a daily window, newest release that is not failed and
  has soaked (first seen N days ago, `seen` in `state.json`), restart only
  after a good install, result in `state.json` (`auto`), `status` and the
  login message.
- `status` shows the unattended setting, its last run and failed versions
  (JSON: `auto`, `failed`, `trial`).

### Changed
- `check --save` records first sightings of releases for the soak window, and
  writes the login message from the state file (a failure outranks
  "available").
- `rollback` drops a recorded trial.

## [0.2.0] — 2026-10-31

### Added
- `status` shows both slots without sudo. `confirm` (every boot), `apply`,
  `rollback` and `status` as root record the slot table in the world-readable
  state file (`slots`, `slots_at`); without root, `status` prints that record,
  with the running slot and its system read live. Pengy asked for a
  passwordless sudo rule just to see the slots.

### Changed
- `confirm` renders grub.cfg with the running system's `render_grub_cfg`
  (`write_boot_menu`, shared with `apply`), at every boot, writing only when it
  differs and leaving a grub.cfg whose kernel line it cannot read alone. A
  boot-menu change used to reach machines one update late, because `apply` runs
  the old version's template.
## [0.1.1] — 2026-09-30

### Fixed
- Use the ESP where it is already mounted (systemd-gpt-auto's `/efi`) instead
  of mounting it a second time read-only, which the kernel refuses.

## [0.1.0] — 2026-09-30

### Added
- `status`, `check`, `apply`, `confirm`, `rollback`, `channel`.
- Atom feed parsing with stable/testing channels; `pengyos-update-1` manifests
  with ed25519 `pengyos-sig-v1` signatures checked through the `openssl` CLI.
- A/B-lite install into the other root slot with a GRUB one-shot trial boot
  (`pengyos_try`) and boot-time confirmation.
