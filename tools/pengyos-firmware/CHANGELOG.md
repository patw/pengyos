# Changelog — pengyos-firmware

## [0.1.0] — 2026-10-15

### Added
- The **Firmware** app (libcosmic): the devices fwupd can update and what they
  run, the updates on offer (current → new, size, urgency, the release's notes),
  the warnings a firmware update needs (AC power, applies while switched off,
  reboot, activation, bootloader mode, replug, locked), **Update all** /
  **Update just this one** with progress, **Restart now** for a staged update,
  snoozing (1 day / 3 days / a week / a month), **Check now**, and a
  notifications switch.
- The session notifier (`--notifier`, XDG autostart): reads root's saved check
  and posts one notification a day per thing — "2 firmware updates are
  available: …" or "Restart to finish a firmware update" — opening the app when
  clicked. Same rules as the PengyOS Updates notifier.
- Root's side, wired in the Desktop overlay: `pengyos-firmware-check.service` +
  `.timer` (daily, saves `/var/lib/pengyos-firmware/state.json`),
  `pengyos-firmware-update@<ids>.service` (installs, streams
  `/run/pengyos-firmware/progress.json`), and
  `60-pengyos-firmware.rules` so wheel at the console starts them without a
  password. `fwupd.service` is enabled in the same overlay: Buildroot's recipe
  installs the unit and enables nothing, so the daemon had never been running.
- Headless modes for SSH and for the units: `check --save`, `apply [ID…]`,
  `status`.
- fwupd 2.x JSON keys (`Version` for the new version, `NewVersion` in 1.x both
  accepted), the AppStream markup in release notes stripped to paragraphs, and
  version comparison only trusted when both sides are dotted decimals — a hex
  version (`1E00`) is fwupd's call, not ours.
