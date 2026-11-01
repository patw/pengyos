# Changelog — pengyos-update


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
