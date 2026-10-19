# `buildroot-external/package/` — the recipes we author

Buildroot 2026.08 ships thousands of packages, but the rule from
[`../../README.md`](../../README.md) is **"we own the recipes"** — and some of
what the PengyOS editions need simply is not in stock Buildroot. This directory
holds the **BR2_EXTERNAL package recipes we author ourselves**: the ones that
make an edition's `BR2_PACKAGE_*` symbols resolvable and its components
buildable from source.

`external.mk` auto-includes every `package/<name>/<name>.mk`; `Config.in` (one
level up) `source`s each `package/<name>/Config.in`, which is what makes the
symbol appear in kconfig.

## What lives here today

Everything an edition needs that stock Buildroot 2026.08 does not package:
COSMIC's native (non-Rust) dependencies, desktop hardware support, fonts and
data, the Pengy agent, and the NVIDIA driver. Which layer turns a recipe on is
in `../configs/layers/*.config`.

| Recipe | Provides | Why we author it |
|---|---|---|
| `greetd/` | `greetd` | The Wayland-ready login manager that starts the COSMIC session. |
| `xdg-desktop-portal/` | `xdg-desktop-portal` | The freedesktop portal broker COSMIC apps talk to. |
| `cosmic-portal/` | COSMIC's portal backend | The COSMIC implementation of the portal interfaces. |
| `xdg-desktop-portal-gtk/` | the GTK portal backend | The portals COSMIC's backend lacks (AppChooser → OpenURI, Print, Notification, Inhibit, …), as Pop!_OS ships it; `portals.conf` prefers COSMIC's where both exist. |
| `dconf/` | the GSettings backend | Writes go through `dconf-service` in order; GLib's keyfile fallback lost cosmic-settings-daemon's concurrent `gsettings set` writes (close-only titlebars). Desktop's `pengyos-gsettings-keyfile-migrate` user unit loads an old keyfile into it. |
| `font-fira/` | Fira Sans + Fira Mono | COSMIC's default interface and monospace fonts. |
| `font-noto-color-emoji/` | Noto Color Emoji | Colour emoji (the single font file from the release tag). |
| `font-noto-symbols2/` | Noto Sans Symbols 2 | Unicode symbols no other shipped font has (U+23F5 media triangles, U+1FB00 legacy-computing graph cells). |
| `font-symbols-nerd/` | Symbols Nerd Font (+ Mono) | The Nerd Fonts icons only (starship's default prompt, powerline, `eza --icons`), found by per-glyph fallback under any terminal font. Upstream's fontconfig file is not installed. |
| `xdg-user-dirs/` | `xdg-user-dirs-update` | Not a stock Buildroot package. Creates ~/Downloads, ~/Documents, …; run at every session start by `start-cosmic`. |
| `flatpak/` | Flatpak 1.18 (CLI, system helper, portal) | Not a stock Buildroot package. The Desktop's app lane (SPEC §2.5); uses the image's bwrap, xdg-dbus-proxy and fusermount3, `wheel` as the admin group. Replaced the build host's copy. |
| `appstream/` | AppStream 1.2 (library + `appstreamcli`) | Not a stock Buildroot package; Flatpak requires it. Patched so a cross build doesn't need a native AppStream. |
| `libfyaml/` | YAML 1.2 parser | Not a stock Buildroot package; AppStream reads YAML with it. |
| `desktop-file-utils-target/` | `update-desktop-database` on the target | Buildroot builds desktop-file-utils for the host only; Flatpak's install trigger needs it. |
| `oo7/` | `oo7-daemon`, `oo7-portal` | The Secret Service (the keyring apps keep passwords in) and its Flatpak portal; replaces gnome-keyring. D-Bus-activated through `oo7-session`, which unseals the keyring password with `systemd-creds` (the desktop logs in without a password). |
| `pop-cursor-theme/` | the Pop mouse cursors and symbolic icons | COSMIC's cursors, from Pop!_OS's icon theme (duplicates as symlinks), the system default cursor theme; and Pop's symbolic icons (`scalable/`, 2.3 MB), which COSMIC's icon theme inherits for icons it does not draw. Pop's fixed-size PNGs are not installed. |
| `zram-generator/` | swap on zram | systemd's generator (Rust), as Fedora/Pop ship it; our default config (zstd, min(RAM, 8 GiB)) and Pop's VM tuning. Core layer: every edition. |
| `sof-firmware/` | Intel SOF audio firmware | DSP firmware + topologies for Intel laptops (digital mics, SoundWire) — the files upstream's `install.sh` copies. |
| `steam-devices/` | Valve's udev rules | Game controllers, VR headsets and `/dev/uinput` for the logged-in user; Steam (a Flatpak) can't install them. |
| `gamemode/` | `gamemoded` | Feral's GameMode, behind the GameMode portal Flatpak games use. |
| `accountsservice/` | `accounts-daemon` | User accounts over D-Bus: COSMIC Settings' Users page. Admin group `wheel`. |
| `switcheroo-control/` | `switcheroo-control` | GPU list for hybrid machines: COSMIC's "Launch using discrete graphics". |
| `rtkit/` | `rtkit-daemon` (RealtimeKit) | Not in Buildroot. Bounded realtime scheduling for unprivileged threads; the Realtime portal Flatpak apps use forwards to it. D-Bus-activated; runs as the `rtkit` user (`RTKIT_USERS`). |
| `geoclue/` | GeoClue 2 + libgeoclue + the demo agent | Not in Buildroot. The location service; `xdg-desktop-portal` compiles its Location portal in only against libgeoclue. Wi-Fi lookups go to BeaconDB (upstream's default); the demo agent (XDG autostart) keeps geoclue from making each client wait 5 s for an agent. |
| `ipp-usb/` | `ipp-usb` (Go) | Driverless USB printers/scanners appear to CUPS/SANE as network devices on localhost; udev-started. |
| `libfreeaptx/`, `ldacbt/` | aptX, LDAC | Bluetooth audio codecs; enabled in PipeWire by `external.mk`. |
| `ffmpegthumbnailer/` | video thumbnails | A freedesktop thumbnailer COSMIC Files runs for videos (PDFs: the overlay's poppler wrapper). |
| `libnotify/` | `notify-send`, libnotify | The command scripts use to raise a desktop notification; Buildroot has no package. |
| `libqalculate/` | `qalc` | The launcher's calculator (pop-launcher's calc plugin runs `qalc`); without it `= 2+2` did nothing. |
| `power-profiles-daemon/` | power profiles over D-Bus | Performance / balanced / power-saver: COSMIC's power menu and Settings → Power. |
| `bolt/` | `boltd`, `boltctl` | Thunderbolt / USB4 device authorization. |
| `ddcutil/` | `ddcutil` | Monitor brightness/input over DDC/CI. |
| `libmtp/` | MTP | Phones and cameras over USB; gvfs's MTP backend (enabled by `external.mk`) so COSMIC Files shows them. |
| `alsa-ucm-conf/` | ALSA UCM profiles | Without them PipeWire exposes only a generic Analog profile: no HDMI/DisplayPort sinks. |
| `distrobox/` | `distrobox` | Server layer: the developer toolchain lane (no compiler on the target; gcc/rustc/… live in a distrobox). |
| `font-noto-cjk/` | Noto Sans CJK | Chinese/Japanese/Korean text, one variable-font collection (32 MB). |
| `xdg-utils/` | `xdg-open`, `xdg-mime`, ... | Not a stock Buildroot package. The scripts' `--help` text is rendered by `docbook2txt.py` (upstream needs `xmlto` + a text browser); a patch lets the generic backend COSMIC runs ask `gio` for file types. |
| `cosmic-sound-theme/` | COSMIC's system sounds | Data-only; aliased as `Pop`, the name COSMIC 1.9 looks sounds up under. |
| `plymouth/` | the boot splash + the PengyOS theme | Not in Buildroot. Pengy on black with a spinner, from early boot until the COSMIC session takes the screen. |
| `pengy/` | Pengy, the built-in agent: `pengy-cli` (Core), `pengy-web` (Server), the Qt6 desktop app (Desktop) | PengyR v1.10.0: one Rust agent core behind all three; the Qt GUI links it statically. Ships unconfigured. |
| `mdit/` | `mdit`, the Markdown editor | Ours (patw/mdit, MIT): a Qt6 Widgets editor with a live preview and HTML/PDF export, built from its release tag with its own `cmake --install` (binary, icons, `.desktop`). Needs Qt6 PrintSupport (enabled in the desktop layer). |
| `cutecosmic/` | Qt platform theme for COSMIC | Qt apps (Pengy) follow COSMIC's dark/light mode, accent, fonts and icons. Patched to build without Qt Quick and with its Rust bindings built offline by cargo instead of a fetched Corrosion. |
| `nvidia-open/` | the NVIDIA driver (NVIDIA edition only) | NVIDIA's open GPU kernel modules + the matching proprietary userspace (EGL/GBM/Wayland, CUDA, NVENC/NVDEC, GSP firmware) from one `.run`. Buildroot's `nvidia-driver` is the legacy 390 branch: no Turing+, no GBM. |

The **COSMIC Rust components themselves** (compositor, session, shell, apps)
have **no recipe here yet**. Today they are built with cargo on the build host
by `scripts/build-cosmic.sh` (`~/cosmic-src/<component>` → `~/cosmic-build/release`) and copied into the
image by `../board/desktop/post-build.sh`, with the patches we carry
in `../board/desktop/cosmic-patches/`; `pengyos-install-gui`,
`pengyos-updates` and `pengyos-disks` are host-built the same way. The `pengyos-cosmic-pkg` /
`pengyos-cosmic-recipes` / `pengyos-cosmic-build` tools are the planned
replacement (cargo recipes with vendored, pinned sources); `Config.in` marks
where their `source` lines go once that lands. (`cosmic-portal` and
`cosmic-sound-theme` above are the exceptions that already have recipes.)

## Conventions for an authored recipe

- A **`.hash`** with a real digest — no unpinned downloads.
- A **license** declared in the `.mk`, matching what the source actually ships.
- The recipe is **linter-clean** (`utils/check-package`) and its symbol
  **resolves** (rung (b)) against the pinned tree.
- Recipes are **reused, never re-derived**: the `*-wire` tools copy the committed
  recipe output byte-for-byte into this tree rather than regenerating it.

> Where the recipe *originates* — which tool authors it, and where its committed
> output lives — is recorded in the sibling tools under [`../../tools/`](../../tools/).
> The integration gate (`scripts/pengyos_gate.py`) checks that every authored
> recipe this tree references actually resolves to a source.
