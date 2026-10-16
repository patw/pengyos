# pengyos-firmware — "Firmware"

The desktop side of **fwupd** (the Linux Vendor Firmware Service client the
Desktop already ships): an app that shows which devices on this machine have a
firmware update, and installs what you ask for — the PengyOS shape of Ubuntu's
"Firmware Updater", with the same notifications as
[`pengyos-updates`](../pengyos-updates/).

- **The app** (`pengyos-firmware`, *Firmware*: in the App Library): the devices
  fwupd can update and what each is running, what is on offer (current → new
  version, size, "Critical security update", the release's own notes), the things
  a firmware update needs from you (plug it into the wall, it applies while the
  computer is switched off, you may have to unplug and replug it), **Update all**,
  **Update just this one**, progress while it runs, **Restart now** when an
  update is staged in the hardware, *Remind me* tomorrow / in 3 days / in a week
  / in a month, **Check now**, and a switch for notifications.
- **The notifier** (`pengyos-firmware --notifier`, from `/etc/xdg/autostart`):
  every 10 minutes it reads root's saved check and your settings and, only when
  wanted, posts one notification — "2 firmware updates are available: System
  Firmware and NVMe SSD", or "Restart to finish a firmware update". Clicking it
  opens the app. Each thing is announced at most once a day; a snoozed set stays
  quiet until the snooze ends, a newer update is announced right away.
- **Root's side** (in the Desktop overlay): `pengyos-firmware-check.timer` runs
  `pengyos-firmware check --save` daily, which asks fwupd (refreshing its
  metadata from LVFS) and saves the answer to
  `/var/lib/pengyos-firmware/state.json`. `pengyos-firmware-update@<ids>.service`
  installs, streaming progress to `/run/pengyos-firmware/progress.json`.
  `/etc/polkit-1/rules.d/60-pengyos-firmware.rules` lets administrators (wheel)
  start both from the app without a password. `fwupd.service` is enabled —
  Buildroot installs the unit and enables nothing, so without that symlink the
  daemon never runs.

The machine checks daily either way; the user's choices
(`~/.config/pengyos-firmware/settings.json`) only decide what is shown.

**Writing firmware is irreversible on hardware.** The app never touches a device:
it proposes, you press the button, a systemd unit runs `fwupdmgr` as root with
the prompts answered, and the hardware does the rest. The unit never restarts the
computer — the app offers *Restart now* when something is staged.

## Build

```console
cargo test                                    # the decision logic, no display needed
CARGO_TARGET_DIR=~/cosmic-build/install-gui cargo build --release --features cosmic
```

The Desktop post-build installs `~/cosmic-build/install-gui/release/pengyos-firmware`.
Without `--features cosmic` the binary prints what the app would show, which is
also what you get over SSH:

```console
$ pengyos-firmware check --save
Checked just now
System Firmware: 0.1.20 -> 0.1.40 (12 MB, Important security update)
  Plug this computer into the wall — it will not update on battery.
  This one applies at the next restart.
would notify: A firmware update is available: System Firmware
```

## Reading it by hand

| Command | What it does |
|---|---|
| `pengyos-firmware status` | what the window shows, as text |
| `pengyos-firmware check --save` | ask fwupd and save the answer (root's daily job) |
| `systemctl start pengyos-firmware-check.service` | check now |
| `systemctl start pengyos-firmware-update@all.service` | install everything on offer |
| `systemctl start pengyos-firmware-update@<DEVICE-ID>.service` | install one device |
| `fwupdmgr get-devices` / `get-updates` / `get-history` | fwupd itself, for the details this app hides |
