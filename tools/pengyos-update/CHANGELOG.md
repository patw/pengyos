# Changelog — pengyos-update

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
