# pengyos-firmware — "Firmware", see what the hardware wants and apply it

## Summary
A libcosmic app for the Desktop that answers the question Ubuntu answers with its
"Firmware Updater" and PengyOS answered for the OS with "PengyOS Updates": **does
this machine have a firmware update waiting, and can I install it from the
desktop?** It lists the updatable devices with their current versions, shows the
updates fwupd found (new version, size, urgency, what the release says), warns
about the things firmware updates really need (AC power, a reboot, a shutdown,
unplugging and replugging), and installs what the user asks for, with progress.
A session notifier says "2 firmware updates are available" the way the PengyOS
Updates notifier says a new release is — same rules, same snoozing, same switch.

## Purpose
The Desktop ships `fwupd` + `fwupd-efi` (UEFI capsules: system firmware, SSDs,
docks, hubs, mice, …) and the fleet has been applying firmware from a terminal
for a year (dbx 20250507→20260402→20260707 fleet-wide; behoweather took UEFI
0.1.20→0.1.40, the dbx and an EC update on 2026-10-03, staged behind a reboot).
Nothing in the Desktop *shows* any of it: a user has `fwupdmgr` in a terminal and
no reason to ever run it, so security-relevant firmware (the UEFI dbx revocation
list is the obvious one) sits unapplied on a machine that is otherwise fully
updated. `fwupd.service` was not even enabled in the image, so the daemon was
not running.

## Deliverable
A Rust crate `pengyos-firmware` (MIT), built exactly like `pengyos-updates`:

- `lib.rs` — std + serde_json, `cargo test`-able with no display, no D-Bus and no
  network: the fwupd JSON parsers (device, release, update state), the merged
  device model, the "what is offered / what is waiting for a reboot" decisions,
  the user's preferences, the notification rules, the progress-line parser, and
  the systemd-unit names.
- `app.rs` (feature `cosmic`) — the window.
- `notifier.rs` (feature `cosmic`) — the session notifier over the freedesktop
  notification bus.
- `main.rs` — `pengyos-firmware` (window, or a text report when built without the
  `cosmic` feature), `--notifier`, `check`, `apply`.

Root's side is three systemd units and one polkit rule in the Desktop overlay:
`pengyos-firmware-check.service` (+ `.timer`, daily) writes
`/var/lib/pengyos-firmware/state.json`; `pengyos-firmware-update@<ids>.service`
installs and writes `/run/pengyos-firmware/progress.json`; wheel at an active
local session may start them without a password.

## Acceptance criteria
1. `cargo test` passes with no display, no network and no fwupd installed.
2. A `get-updates`/`get-devices` JSON pair parses into the device model: current
   and new version, size, urgency, categories, and the flags the app must warn
   about (`require-ac`, `needs-shutdown`, `needs-reboot`, `needs-activation`,
   `locked`, `needs-bootloader`, `wait-for-replug`).
3. Nothing is offered for a device that is not `updatable`, for a bootloader-mode
   device, or for a device whose newest release is not newer than what runs.
4. A staged update (a device flagged `needs-reboot` with a pending/successful
   result) is reported as "restart to finish", not as a new update.
5. Notifications follow the PengyOS Updates rules: off is off; one reminder a day
   per thing; "remind me" 1 day / 3 days / a week / a month; a *newer* update than
   the snoozed one is announced immediately.
6. The app never touches hardware by itself: installing is a systemd unit the
   user starts, and the unit runs `fwupdmgr` with the prompts answered and the
   reboot left to the user.
7. The app is wired in all three places at once (build list, post-build install,
   `.desktop` launcher) and the notifier has an autostart entry — the wiring test
   in `scripts/tests/test_cosmic_app_wiring.py` enforces it.
8. `fwupd.service` is enabled in the Desktop image, so the daemon the app reads
   is actually running.

## Not in scope
BIOS settings (`fwupdmgr get-bios-settings`), firmware *history*, downgrades,
branch switching, remote management, and any device unlock. Those stay in the
terminal. Writing firmware is irreversible on hardware: the app proposes, the
person clicks, `fwupd` and the hardware do the rest.
