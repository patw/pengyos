# PengyOS Skill

**How this machine works.** PengyOS is not a Debian, Ubuntu, Fedora or Arch
derivative — it has its own storage model, its own update path, and no package
manager in the familiar sense. Answering a PengyOS question with generic Linux
habits is the main way to get it wrong, so read this before touching system
files, installing anything, or explaining what you are running.

```sh
cat /etc/os-release          # edition, IMAGE_ID, IMAGE_VERSION, BUILD_ID
cat /proc/cmdline            # which root slot booted, and that this is PengyOS
ls /etc/pengyos/             # this machine's own config
```

## What it is

A Linux distribution built **from source with Buildroot** — assembled, not
derived. Every component is chosen on its merits and justified in writing; the
image ships with an SBOM and a signed attestation. Three editions that nest:

```
CORE   = the base: systemd, key-only sshd, Python 3, a default-deny firewall
         (pengyos-firewall opens a port), chrony, firmware
SERVER = Core + tmux/mosh/zsh/git/ripgrep/fd/bat/eza/zoxide/jq (+ fzf, starship and the zsh plugins on the comfort profile), rootless podman,
         distrobox, backup + monitoring timers,
         the daily update check, pengy-web, Wi-Fi (wpa_supplicant), laptop-as-server
         care (lid ignored, battery limit, screen off when idle)
DESKTOP= Server + every port above 1024 open, the COSMIC Wayland session, PipeWire, BlueZ, CUPS, Wi-Fi,
         Mesa/Vulkan, Flatpak + Flathub, Plymouth splash, the Pengy app
```

`…-nvidia` variants of Server and Desktop carry NVIDIA's open driver (Turing and
newer). x86_64 UEFI only.

## The storage model — the part that changes every answer

```
ESP        vfat   GRUB, grub.cfg, grubenv (which slot boots)
root-a     ext4   the system image, mounted READ-ONLY   ← /
root-b     ext4   empty until the first update
data       btrfs  everything that is *this machine*     ← never touched by an update
```

`data` holds one btrfs subvolume per area — `etc`, `var`, `home`, `root` — and
the system assembles them at boot (`init=/usr/lib/pengyos/preinit`):

- **`/` is the read-only image.** `/usr` cannot be written, not even by root.
- **`/etc` is an overlay**: the image's `/etc` as the lower layer,
  `/data/etc/upper` as the upper. Your edits land on `data` and survive updates.
  `mount | grep ' /etc '` showing `overlay` is the fingerprint of a PengyOS box.
- `/home`, `/var`, `/root` are binds of the `data` subvolumes.
- Swap is zram.

**Three tiers, and they decide what you may change:**

| Tier | Surface | Who changes it |
|---|---|---|
| 0 — base | kernel, libraries, the image | nobody at runtime; a signed image written to the other slot |
| 1 — machine | `/etc`, services, users' data, firewall, units | the admin (or Pengy), freely — it is all on `data` |
| 2 — trust root | signing keys, accepting a release, privilege changes | a human signs; never automate these |

## Updates

```sh
pengyos-update status            # version, channel, slots (sudo for the slots)
pengyos-update check             # is a newer signed release out?
sudo pengyos-update apply        # write it to the other slot and boot it once on trial
pengyos-update confirm           # keep the slot that just booted (runs at boot)
pengyos-update rollback          # make the other slot the default again
sudo pengyos-update channel      # testing | stable  (writes /etc/pengyos/update.conf)
```

An update that fails to reach `multi-user.target` falls back to the old slot by
itself. Releases are checked against PengyOS's signing key in
`/usr/share/pengyos/keys` before anything is written. **`apply` changes the
running system — confirm the user wants it first.** Core cannot update in place
yet.

## Where to find everything

| Path | What |
|---|---|
| `/etc/os-release` | `ID=pengyos`, `VARIANT`, `IMAGE_ID`, `IMAGE_VERSION`, `BUILD_ID` |
| `/etc/pengyos/` | this machine's config: `update.conf`, `autologin.conf`, `backup.conf`, `monitor.conf`, `buildroot.config` |
| `/usr/lib/pengyos/` | the boot machinery: `preinit`, the `update.d/` steps, session scripts |
| `/usr/share/pengyos/` | signing keys, guest definitions (`guests.d/`) |
| `/data/` | the btrfs data partition (mounted as `/etc` upper, `/home`, `/var`) |
| `/efi` | the ESP, automounted on access (fwupd stages UEFI capsules here) |
| `/etc/skel/` | what a new user's home starts with (`.zshrc`, `.bashrc`, `skills/`) |
| `~/.config/pengy/` | Pengy's config + chats; `~/skills/` for the skills |
| `/usr/libexec/pengyos/` | the helpers behind the panels and the installer |

The **source of truth is the repository**, not this machine: `SPEC.md`,
`docs/`, `tools/` (one tested project per directory), `buildroot-external/`
(boards, defconfigs, recipes). It lives on the build host at `/data/PengyOS`;
`/etc/pengyos/buildroot.config` records which config built this image. Public
site: <https://pengyos.catbee.ca>.

## Commands this image ships

| Command | What |
|---|---|
| `pengy-cli` / `pengy-web` / `pengy` | the assistant (CLI / web / Desktop app) |
| `pengyos-update` | the updater (above) |
| `pengyos-install` | the install engine (`disks`, `image`) — what the installers drive |
| `pengyos-install-tui` | the console installer (`install`) |
| `pengyos-autologin on\|off` | Desktop: skip the login screen at boot and the lock screen on sleep/idle |
| `pengyos-webui status\|on\|off` | Server/Desktop: serve the Pengy web UI as a user, on `http://127.0.0.1:5000` (no login — see Gotchas) |
| `pengyos-guest list\|enter <name>` | declarative distrobox guests; `dev` is the box with compilers |
| `distrobox create\|enter` | a full distro userspace sharing `$HOME` |
| `flatpak install flathub <app>` | third-party GUI apps (Desktop) |

## How to install things — four lanes, in trust order

There is **no apt, dpkg, pacman or dnf here, and `/usr` is read-only.** Never
propose `apt install`, `pip install` into system paths, or editing files under
`/usr` as a fix.

1. **Recipes (Lane 1)** — everything with source is a Buildroot recipe in the
   repo. Adding a package means changing the definition and building a new
   image; it is build-host work, not a change to a running machine.
2. **Upstream artifacts (Lane 2)** — a vendor's own prebuilt binary, pinned by
   hash and isolated in its own prefix.
3. **Sandboxed apps (Lane 3)** — **Flatpak** from Flathub is the primary way to
   add a GUI app on Desktop (Firefox lands on first boot); AppImage works too
   (FUSE is present). On Desktop, `pengyos-appimage integrate APP.AppImage`
   adds one to the app launcher where it sits, and when one will not start,
   `pengyos-appimage check APP.AppImage` names the libraries it needs that the
   image lacks (exit 4). It never runs the AppImage.
4. **Guests (Lane 4)** — anything needing a full distro userspace: `podman`
   rootless, `distrobox`, `pengyos-guest`. **Compilers and build toolchains live
   in the `dev` guest** (`pengyos-guest enter dev`), which shares `$HOME` and
   feels native — the base image has no compiler by design.

For a user's own tools, `pip install --user`, `venv`, and `uv` work against
Core's Python. Installing a binary that runs with system access is a Tier-2 act
— say so and let the human decide.

## Gotchas

- **`pengyos-webui` serves a UI with no login.** `sudo pengyos-webui on
  [USER]` runs `pengy-web` as that user at boot, on `127.0.0.1:5000`; `off`
  stops it, `status [--json]` says what is running. The UI has no login and
  exposes `run_bash`/`run_python`, so anyone who can reach the port runs tools
  as that user: loopback is the only default, `--host` is an unauthenticated
  network bind that has to be typed on purpose, and `status` prints the bind.
  State is `/etc/pengyos/webui.conf` (Tier 1, survives updates). Core has no
  `pengy-web` and no `pengyos-webui`.
- **`/usr` is read-only.** A "fix" that edits `/usr` cannot work; the mutable
  surface is `/etc` (Tier 1) and the data partition.
- **Scheduling is systemd timers; there is no cron or `at`.** A recurring job
  is a `.service` + `.timer` pair (`OnCalendar=`, `Persistent=true`) in
  `~/.config/systemd/user/` (as the user; `loginctl enable-linger` once so it
  runs while logged out) or `/etc/systemd/system/` (as root). A one-off is
  `systemd-run --user --on-active=2h CMD`. Check a schedule with
  `systemd-analyze calendar '...'`; the `crontab` command prints the mapping.
  Cron inside a distrobox guest never fires (no init there).
- **Wi-Fi on the Server is wpa_supplicant + networkd, not NetworkManager.**
  One `wpa_supplicant-nl80211@<iface>.service` per Wi-Fi interface, config in
  `/etc/wpa_supplicant/wpa_supplicant-nl80211-<iface>.conf` (root-only: it holds
  the password; the installer writes it for the network it joined). Join or
  change networks with `sudo wpa_cli -i <iface>` (`scan`, `scan_results`,
  `add_network`, `set_network`, `save_config`); networkd gives the address
  (`networkctl status <iface>`). `iw dev` shows the radio and its link. There
  is no `nmcli` on the Server.
- **A laptop server ignores its lid, holds its battery at 80% and blanks its
  screen.** Closing the lid does nothing (`/usr/lib/systemd/logind.conf.d/
  60-pengyos-lid.conf`; override in `/etc/systemd/logind.conf.d/`). The charge
  limit is `LIMIT=` in `/etc/pengyos/battery.conf` (100 = charge to full), applied
  by `sudo systemctl restart pengyos-battery-limit` and only where the laptop's
  platform driver offers `charge_control_end_threshold` (`journalctl -u
  pengyos-battery-limit` says which). The console screen switches off after 5
  idle minutes (`pengyos-console-blank.service`); a key brings it back. None of
  this runs on the Desktop.
- **sshd is key-only** (password logins over SSH are off). A user with no key in
  `~/.ssh/authorized_keys` can log in at the console and not over SSH.
- **The Desktop's COSMIC pieces are built on the build host**, not in the image;
  a Desktop machine has no COSMIC source and no cargo toolchain for it.
- **Reboots, firmware writes, destructive removals, keys and spending are the
  operator's call** in every zone — propose them, don't run them.
- **A live fix belongs in the repo.** Something you repaired on this machine
  (a fragment, an overlay file, a unit) is worth carrying back into the
  definition so the next image already has it.
