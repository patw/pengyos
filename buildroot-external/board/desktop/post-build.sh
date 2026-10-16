#!/bin/sh
# PengyOS Desktop — post-build (runs after the rootfs is assembled).
#
# Runs AFTER Core's and Server's post-build (configs/layers/ stacks the
# scripts). Core's does the common work (SSH keys, host keys, provenance, SBOM,
# tool stubs); this one makes the image the Desktop: hostname, os-release, GRUB
# menu, COSMIC, the installer, the hardware and developer bits.
set -e

TARGET_DIR="$1"

# COSMIC and the PengyOS GUI apps are built with cargo on the build host, not by
# Buildroot (yet): sources in $PENGYOS_COSMIC_SRC, cargo's target directory in
# $PENGYOS_COSMIC_BUILD. See the build guide (Build COSMIC on the host).
COSMIC_SRC="${PENGYOS_COSMIC_SRC:-$HOME/cosmic-src}"
COSMIC_BUILD="${PENGYOS_COSMIC_BUILD:-$HOME/cosmic-build}"

# Hostname (belt-and-braces alongside BR2_TARGET_GENERIC_HOSTNAME).
echo "pengyos-desktop" > "${TARGET_DIR}/etc/hostname"

# --- Brand /etc/os-release as PengyOS Desktop ------------------------------------
# Buildroot ships a generic "Buildroot <version>" os-release; board/common's
# writer replaces it (and the /usr/lib copy) from the repository's VERSION.
# The NVIDIA edition shares this board: name it apart (IMAGE_ID is what an
# update feed matches, and an NVIDIA machine must only take NVIDIA images).
if [ -x "${TARGET_DIR}/usr/bin/nvidia-smi" ]; then
    sh "$(dirname "$0")/../common/os-release.sh" "${TARGET_DIR}" "Desktop NVIDIA" desktop-nvidia
else
    sh "$(dirname "$0")/../common/os-release.sh" "${TARGET_DIR}" Desktop desktop
fi

# Bootable image: install our GRUB menu once grub2 has staged efi-part/ in
# BINARIES_DIR (same pattern as Core). Requires BR2_TARGET_GRUB2_X86_64_EFI.
BOARD_DIR=$(dirname "$0")
if [ -d "${BINARIES_DIR}/efi-part/" ]; then
    cp -f "${BOARD_DIR}/grub-efi.cfg" "${BINARIES_DIR}/efi-part/EFI/BOOT/grub.cfg"
    cp -f "${BOARD_DIR}/grub-pengyos.pf2" "${BINARIES_DIR}/efi-part/EFI/BOOT/grub-pengyos.pf2"
fi

# --- COSMIC: install the host-built binaries ---------------------------------
# Built on the build host ($COSMIC_BUILD/release) and copied in here; this goes
# away once COSMIC has Buildroot recipes (tools/pengyos-cosmic-build). Guarded:
# a host without the build still produces an image, with no desktop session.
COSMIC_REL="${COSMIC_BUILD}/release"
if [ -d "${COSMIC_REL}" ]; then
    n=0
    for b in cosmic-comp cosmic-session cosmic-panel cosmic-panel-button \
             cosmic-launcher cosmic-applets cosmic-settings cosmic-settings-daemon \
             cosmic-notifications cosmic-osd cosmic-osk cosmic-idle cosmic-bg \
             cosmic-randr cosmic-screenshot \
             cosmic-term cosmic-files cosmic-edit cosmic-store cosmic-player cosmic-app-library \
             cosmic-workspaces cosmic-viewer cosmic-greeter cosmic-monitor \
             cosmic-files-applet cosmic-greeter-daemon; do
        if [ -x "${COSMIC_REL}/${b}" ]; then
            cp -f "${COSMIC_REL}/${b}" "${TARGET_DIR}/usr/bin/${b}"
            chmod 755 "${TARGET_DIR}/usr/bin/${b}"
            n=$((n + 1))
        fi
    done
    echo "post-build: installed ${n} COSMIC binaries from ${COSMIC_REL}"
else
    echo "post-build: ${COSMIC_REL} absent — no COSMIC binaries installed (no desktop session)"
fi

# --- pop-launcher: the search backend behind cosmic-launcher -----------------
# cosmic-launcher (Super, the dock's launcher button, alt-tab) is only a frontend;
# it spawns `pop-launcher` and renders its results. Without it the launcher
# logged "pop-launcher failed to start" and never opened. Mirrors the
# pop-os/launcher justfile `install` with rootdir=/usr: one binary, every plugin
# a <plugin-dir>/<name>/<name> symlink to it next to its plugin.ron, + scripts.
POP_LAUNCHER_SRC="${COSMIC_SRC}/pop-launcher"
if [ -x "${COSMIC_REL}/pop-launcher-bin" ] && [ -d "${POP_LAUNCHER_SRC}/plugins/src" ]; then
    install -Dm0755 "${COSMIC_REL}/pop-launcher-bin" "${TARGET_DIR}/usr/bin/pop-launcher"
    PL_DIR="${TARGET_DIR}/usr/lib/pop-launcher"
    for plugin in calc desktop_entries files find pop_shell pulse recent scripts terminal web cosmic_toplevel; do
        mkdir -p "${PL_DIR}/plugins/${plugin}"
        install -m0644 "${POP_LAUNCHER_SRC}/plugins/src/${plugin}"/*.ron "${PL_DIR}/plugins/${plugin}/"
        ln -sf ../../../../bin/pop-launcher "${PL_DIR}/plugins/${plugin}/$(echo "${plugin}" | sed 's/_/-/')"
    done
    mkdir -p "${PL_DIR}/scripts"
    cp -r "${POP_LAUNCHER_SRC}/scripts/." "${PL_DIR}/scripts/"
    echo "post-build: installed pop-launcher + plugins"
else
    echo "post-build: pop-launcher not built (${COSMIC_REL}/pop-launcher-bin) — the launcher will NOT open"
fi

# Host runtime libs cosmic-comp needs that the image doesn't build (until COSMIC
# has recipes, which will provide them). Copying the host copies
# guarantees the SONAME the host-built binaries were linked against.
for so in libdisplay-info.so.3 libgudev-1.0.so.0 libmtdev.so.1 libwacom.so.9 libpipewire-0.3.so.0 libdav1d.so.7; do
    # Skip when a Buildroot package already provided it (avoid a stale copy).
    if [ -e "${TARGET_DIR}/usr/lib/${so}" ]; then
        continue
    fi
    src=""
    for d in /usr/lib/x86_64-linux-gnu /lib/x86_64-linux-gnu /usr/lib; do
        [ -e "${d}/${so}" ] && { src="${d}/${so}"; break; }
    done
    if [ -n "${src}" ]; then
        cp -fL "${src}" "${TARGET_DIR}/usr/lib/${so}"
        echo "post-build: copied host lib ${so}"
    fi
done

# --- The graphical installer ----------------------------------------------------
# The engine + console installer come from Server's post-build. The libcosmic
# GUI is host-built like the COSMIC apps (tools/pengyos-install-gui: cargo build
# --release --features cosmic, CARGO_TARGET_DIR=~/cosmic-build/install-gui); the
# live medium starts it (pengyos-live-installer.service).
INSTALL_GUI="${COSMIC_BUILD}/install-gui/release/pengyos-install-gui"
if [ -x "${INSTALL_GUI}" ]; then
    install -Dm0755 "${INSTALL_GUI}" "${TARGET_DIR}/usr/bin/pengyos-install-gui"
    echo "post-build: installed pengyos-install-gui"
else
    echo "post-build: ${INSTALL_GUI} not built — the USB stick will have no graphical installer"
fi

# --- PengyOS Updates (app + notifier) -------------------------------------------
# tools/pengyos-updates, host-built the same way (cargo build --release
# --features cosmic, same CARGO_TARGET_DIR, so it shares the libcosmic build).
# The .desktop files are in the overlay; root's side is pengyos-update.
UPDATES_GUI="${COSMIC_BUILD}/install-gui/release/pengyos-updates"
if [ -x "${UPDATES_GUI}" ]; then
    install -Dm0755 "${UPDATES_GUI}" "${TARGET_DIR}/usr/bin/pengyos-updates"
    echo "post-build: installed pengyos-updates"
else
    echo "post-build: ${UPDATES_GUI} not built — no update app or notifications"
fi

# --- Disks (partition and format drives, write images, eject) ----------------
# tools/pengyos-disks, host-built the same way. It only talks to UDisks2 (in the
# desktop layer); the .desktop file is in the overlay.
DISKS_GUI="${COSMIC_BUILD}/install-gui/release/pengyos-disks"
if [ -x "${DISKS_GUI}" ]; then
    install -Dm0755 "${DISKS_GUI}" "${TARGET_DIR}/usr/bin/pengyos-disks"
    echo "post-build: installed pengyos-disks"
else
    echo "post-build: ${DISKS_GUI} not built — no Disks app (partitioning needs a terminal)"
fi

# --- Disk Usage (what is taking the space, as a ring chart) -------------------
# tools/pengyos-usage, host-built the same way. Read-only and unprivileged: it
# walks what the user can read. The .desktop file is in the overlay.
USAGE_GUI="${COSMIC_BUILD}/install-gui/release/pengyos-usage"
if [ -x "${USAGE_GUI}" ]; then
    install -Dm0755 "${USAGE_GUI}" "${TARGET_DIR}/usr/bin/pengyos-usage"
    echo "post-build: installed pengyos-usage"
else
    echo "post-build: ${USAGE_GUI} not built — no Disk Usage app"
fi

# --- Gamepads (list the controllers and test their controls) ----------------
# tools/pengyos-gamepads, host-built the same way. It reads /dev/input through
# sysfs (enumeration) and evdev (the live panel); the .desktop file is in the
# overlay and the `input` group is in board/desktop/users.table.
GAMEPADS_GUI="${COSMIC_BUILD}/install-gui/release/pengyos-gamepads"
if [ -x "${GAMEPADS_GUI}" ]; then
    install -Dm0755 "${GAMEPADS_GUI}" "${TARGET_DIR}/usr/bin/pengyos-gamepads"
    echo "post-build: installed pengyos-gamepads"
else
    echo "post-build: ${GAMEPADS_GUI} not built — no Gamepads app (controllers are invisible)"
fi

# --- Printers (add/remove printers, the print queue, test pages) ---------------
# tools/pengyos-printers, host-built the same way. It talks IPP to cupsd on its
# local socket; wheel members administer CUPS (SystemGroup, below) with no
# password, by peer credentials. The .desktop file is in the overlay.
PRINTERS_GUI="${COSMIC_BUILD}/install-gui/release/pengyos-printers"
if [ -x "${PRINTERS_GUI}" ]; then
    install -Dm0755 "${PRINTERS_GUI}" "${TARGET_DIR}/usr/bin/pengyos-printers"
    echo "post-build: installed pengyos-printers"
else
    echo "post-build: ${PRINTERS_GUI} not built — no Printers app (adding a printer needs a terminal)"
fi

# --- Firmware (fwupd: device firmware updates) -------------------------------
# tools/pengyos-firmware, host-built the same way. The app and its session
# notifier are unprivileged; root's side is pengyos-firmware-check.service and
# pengyos-firmware-update@.service (overlay), which run this same binary. The
# .desktop file, the autostart entry and the polkit rule are in the overlay.
FIRMWARE_GUI="${COSMIC_BUILD}/install-gui/release/pengyos-firmware"
if [ -x "${FIRMWARE_GUI}" ]; then
    install -Dm0755 "${FIRMWARE_GUI}" "${TARGET_DIR}/usr/bin/pengyos-firmware"
    echo "post-build: installed pengyos-firmware"
else
    echo "post-build: ${FIRMWARE_GUI} not built — no Firmware app (firmware updates need a terminal)"
fi

# --- Strip the host-built binaries ---------------------------------------------
# Buildroot strips target binaries before post-build runs, so everything copied in
# above (COSMIC, pop-launcher, the installer GUI) arrived with its symbol tables.
STRIP_BIN="$(ls "${HOST_DIR}"/bin/*-buildroot-linux-gnu-strip 2>/dev/null | head -1)"
if [ -n "${STRIP_BIN}" ]; then
    for f in "${TARGET_DIR}"/usr/bin/cosmic-* "${TARGET_DIR}"/usr/bin/pop-launcher \
             "${TARGET_DIR}"/usr/bin/pengyos-install-gui "${TARGET_DIR}"/usr/bin/pengyos-updates \
             "${TARGET_DIR}"/usr/bin/pengyos-disks "${TARGET_DIR}"/usr/bin/pengyos-usage \
             "${TARGET_DIR}"/usr/bin/pengyos-gamepads \
             "${TARGET_DIR}"/usr/bin/pengyos-printers "${TARGET_DIR}"/usr/bin/pengyos-firmware; do
        [ -f "$f" ] && [ ! -L "$f" ] && file -b "$f" | grep -q "^ELF" && "${STRIP_BIN}" "$f"
    done
    echo "post-build: stripped host-built binaries"
fi

# --- COSMIC portal data files (the cargo package installs only the binary) ---
# cosmic.portal + portals.conf + the dbus service let xdg-desktop-portal route
# to the COSMIC backend (Screenshot/ScreenCast/Settings/FileChooser/...).
PCDIR="$(find "${BUILD_DIR}" -maxdepth 1 -type d -name 'cosmic-portal-*' 2>/dev/null | head -1)/data"
if [ -d "${PCDIR}" ]; then
    mkdir -p "${TARGET_DIR}/usr/share/xdg-desktop-portal/portals" \
             "${TARGET_DIR}/usr/share/dbus-1/services"
    cp -f "${PCDIR}/cosmic.portal" "${TARGET_DIR}/usr/share/xdg-desktop-portal/portals/cosmic.portal"
    cp -f "${PCDIR}/cosmic-portals.conf" "${TARGET_DIR}/usr/share/xdg-desktop-portal/portals.conf"
    # D-Bus *activation* file (the upstream .service.in is a systemd unit, which
    # a plain dbus-daemon session bus cannot use).
    cat > "${TARGET_DIR}/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.cosmic.service" <<'DBUSEOF'
[D-BUS Service]
Name=org.freedesktop.impl.portal.desktop.cosmic
Exec=/usr/bin/xdg-desktop-portal-cosmic
DBUSEOF
    echo "post-build: installed COSMIC portal data (cosmic.portal + dbus service)"
else
    echo "post-build: cosmic-portal data not found at ${PCDIR} (portal backend data skipped)"
fi

# --- Background portal backend (org.freedesktop.impl.portal.Background) --------
# xdg-desktop-portal exports org.freedesktop.portal.Background only if some
# backend declares it: init_background() returns as soon as no .portal file names
# the interface. Neither COSMIC's backend nor portal-gtk declares it, so the
# interface does not exist and flatpak apps that ask to run in the background or
# to autostart fail with UnknownMethod and report it as a denied permission
# (SyncThingy on 26.10.13: "Background permission request failed").
#
# The core already does the permission store, the Access dialog and writing
# ~/.config/autostart/<id>.desktop with a flatpak-rewritten Exec; the backend
# only answers GetAppState and NotifyBackground. Why each answer is what it is:
# board/desktop/background-portal/README.md.
BG_SRC="${BOARD_DIR}/background-portal/background-portal.c"
BG_BIN="${TARGET_DIR}/usr/libexec/pengyos/background-portal"
BG_CC="$(ls "${HOST_DIR}"/bin/*-buildroot-linux-gnu-gcc 2>/dev/null | head -1)"
if [ -z "${BG_CC}" ] || [ ! -f "${BG_SRC}" ] || [ -z "${STAGING_DIR}" ]; then
    echo "post-build: Background portal NOT built (need a cross gcc, ${BG_SRC} and STAGING_DIR) — flatpak autostart will fail" >&2
else
    mkdir -p "${TARGET_DIR}/usr/libexec/pengyos"
    PKG_CONFIG_SYSROOT_DIR="${STAGING_DIR}" \
    PKG_CONFIG_PATH="${STAGING_DIR}/usr/lib/pkgconfig" \
    PKG_CONFIG_LIBDIR="${STAGING_DIR}/usr/lib/pkgconfig" \
        "${BG_CC}" -Wall -O2 -o "${BG_BIN}" "${BG_SRC}" \
            $("${HOST_DIR}/bin/pkg-config" --cflags --libs gio-2.0) \
        || { echo "post-build: Background portal failed to compile" >&2; exit 1; }
    "${BG_CC%gcc*}strip" "${BG_BIN}" 2>/dev/null || true
    echo "post-build: built the Background portal backend"

    # Route it. The .portal file (which declares the interface) and the D-Bus
    # activation file come from the overlay; this names it for the interface in
    # the portals.conf installed above — the same mechanism the Secret portal
    # already uses for oo7-portal.
    BG_CONF="${TARGET_DIR}/usr/share/xdg-desktop-portal/portals.conf"
    if [ -f "${BG_CONF}" ]; then
        grep -q "impl.portal.Background" "${BG_CONF}" || \
            echo "org.freedesktop.impl.portal.Background=pengyos-background;" >> "${BG_CONF}"
        echo "post-build: portals.conf routes Background to pengyos-background"
    else
        echo "post-build: no portals.conf to route Background in" >&2
    fi
fi

# --- COSMIC shell defaults: panel layout / dock / applets --------------------
# cosmic-panel ships its default config as `data/default_schema/` and its own
# install rule copies it to <prefix>/share/cosmic/ (see cosmic-panel/justfile:
# `default-schema-target := INSTALL_DIR / 'cosmic'`). `cargo install` does NOT
# install data files, so without this the panel is EMPTY (cosmic-panel
# logged "Panel Entry Error: NoConfigDirectory", "Falling back to default panel
# configuration") and no dock. Copy the schema from the checked-out source tree
# so COSMIC shows its Panel (top bar + clock) and Dock.
COSMIC_PANEL_SRC="${COSMIC_SRC}/cosmic-panel/data/default_schema"
if [ -d "${COSMIC_PANEL_SRC}" ]; then
    mkdir -p "${TARGET_DIR}/usr/share/cosmic"
    cp -a "${COSMIC_PANEL_SRC}/." "${TARGET_DIR}/usr/share/cosmic/"
    # The checked-out schema lags the code: cosmic-panel reads padding_overlap
    # and keep_style_on_maximize but the schema has neither ("Panel Entry Error:
    # GetKey(...)"). Missing keys make the panel write its whole config into
    # ~/.config on first start and reload itself mid-startup off its own writes;
    # on the N100 the first boot then came up with no dock and a panel that
    # ignored clicks (later boots, with the config written, were fine). Complete
    # the defaults with the values the panel itself writes.
    for e in Panel Dock; do
        d="${TARGET_DIR}/usr/share/cosmic/com.system76.CosmicPanel.${e}/v1"
        [ -e "$d/padding_overlap" ] || echo 0.5 > "$d/padding_overlap"
        [ -e "$d/keep_style_on_maximize" ] || echo false > "$d/keep_style_on_maximize"
    done
    echo "post-build: installed cosmic-panel default_schema -> /usr/share/cosmic/"
else
    echo "post-build: ${COSMIC_PANEL_SRC} absent — COSMIC panel defaults NOT installed (panel will be empty)"
fi

# --- cosmic-settings data: theme defaults, illustrations, polkit -------------
# Mirrors cosmic-settings' `just install`, which `cargo install` skips:
#  - resources/default_schema -> /usr/share/cosmic: the system CosmicTheme.Dark/
#    Light(+.Builder)/Mode defaults. Without them libcosmic filled every Light
#    key from its dark default, so choosing Light re-themed nothing — and
#    Settings then wrote that dark "Light" theme into ~/.config, which it reads
#    back on every change (an install that was clicked before this fix must drop
#    ~/.config/cosmic/com.system76.CosmicTheme.* once).
#  - resources/icons -> hicolor: the Appearance page's Dark/Light and style
#    illustrations (scalable/status). Missing, the mode buttons collapsed to
#    empty 2px bars.
#  - the polkit rule + action for the Users page.
COSMIC_SETTINGS_SRC="${COSMIC_SRC}/cosmic-settings/resources"
if [ -d "${COSMIC_SETTINGS_SRC}/default_schema" ]; then
    mkdir -p "${TARGET_DIR}/usr/share/cosmic" "${TARGET_DIR}/usr/share/icons/hicolor"
    cp -a "${COSMIC_SETTINGS_SRC}/default_schema/." "${TARGET_DIR}/usr/share/cosmic/"
    cp -a "${COSMIC_SETTINGS_SRC}/icons/." "${TARGET_DIR}/usr/share/icons/hicolor/"
    install -Dm0644 "${COSMIC_SETTINGS_SRC}/polkit-1/rules.d/cosmic-settings.rules" \
        "${TARGET_DIR}/usr/share/polkit-1/rules.d/cosmic-settings.rules"
    for a in "${COSMIC_SETTINGS_SRC}"/polkit-1/actions/*.policy; do
        install -Dm0644 "$a" "${TARGET_DIR}/usr/share/polkit-1/actions/$(basename "$a")"
    done
    # PengyOS look: light mode, green accent, slightly round (theme-defaults/).
    cp -r "$(dirname "$0")/theme-defaults/." "${TARGET_DIR}/usr/share/cosmic/"
    rm -f "${TARGET_DIR}/usr/share/cosmic/README.md"
    echo "post-build: installed cosmic-settings theme defaults + illustrations + polkit"
else
    echo "post-build: ${COSMIC_SETTINGS_SRC} absent — theme defaults NOT installed (Light mode will render dark)"
fi

# --- Keyboard shortcuts: cosmic-comp + cosmic-settings-daemon data -----------
# The default keybindings (Super -> launcher, Super+T -> terminal, workspace and
# tiling keys, ...) are cosmic-comp's data/keybindings.ron, and what each system
# action runs is cosmic-settings-daemon's data/system_actions.ron — both placed
# by their Makefile `install`, not by cargo. Missing, every compositor shortcut
# was dead ("shortcuts defaults config error: NoConfigDirectory").
COSMIC_COMP_SRC="${COSMIC_SRC}/cosmic-comp/data"
COSMIC_SCD_SRC="${COSMIC_SRC}/cosmic-settings-daemon/data"
if [ -f "${COSMIC_COMP_SRC}/keybindings.ron" ] && [ -f "${COSMIC_SCD_SRC}/system_actions.ron" ]; then
    install -Dm0644 "${COSMIC_COMP_SRC}/keybindings.ron" \
        "${TARGET_DIR}/usr/share/cosmic/com.system76.CosmicSettings.Shortcuts/v1/defaults"
    install -Dm0644 "${COSMIC_COMP_SRC}/tiling-exceptions.ron" \
        "${TARGET_DIR}/usr/share/cosmic/com.system76.CosmicSettings.WindowRules/v1/tiling_exception_defaults"
    install -Dm0644 "${COSMIC_SCD_SRC}/system_actions.ron" \
        "${TARGET_DIR}/usr/share/cosmic/com.system76.CosmicSettings.Shortcuts/v1/system_actions"
    install -Dm0644 "${COSMIC_SCD_SRC}/polkit-1/rules.d/cosmic-settings-daemon.rules" \
        "${TARGET_DIR}/usr/share/polkit-1/rules.d/cosmic-settings-daemon.rules"
    echo "post-build: installed COSMIC keybindings + system actions + tiling exceptions"
else
    echo "post-build: cosmic-comp / cosmic-settings-daemon data absent — keyboard shortcuts will NOT work"
fi

# --- COSMIC applets: desktop files + multi-call symlinks ---------------------
# cosmic-panel resolves each panel plugin by its <plugin-id>.desktop entry in
# /usr/share/applications and runs that entry's Exec (e.g. cosmic-applet-time,
# cosmic-app-list). Every applet is the SAME multi-call binary (cosmic-applets, a
# busybox-style dispatcher on argv[0]); `cargo install` installs neither the data
# files nor the argv[0] symlinks. Without both, the panel resolves ZERO plugins,
# so each layer surface has zero-size content and never attaches a buffer — the
# bar simply never appears (observed 2026-09-25).
COSMIC_APPLETS_SRC="${COSMIC_SRC}/cosmic-applets"
if [ -d "${COSMIC_APPLETS_SRC}" ] && [ -x "${TARGET_DIR}/usr/bin/cosmic-applets" ]; then
    for f in "${COSMIC_APPLETS_SRC}"/*/data/*.desktop; do
        [ -e "$f" ] || continue
        install -Dm0644 "$f" "${TARGET_DIR}/usr/share/applications/$(basename "$f")"
    done
    for name in $(grep -rhoE '^Exec=[A-Za-z0-9._-]+' "${COSMIC_APPLETS_SRC}"/*/data/*.desktop | sed 's/^Exec=//' | sort -u); do
        case "$name" in
            cosmic-panel-button) continue ;;  # separate binary, already installed
        esac
        ln -sf cosmic-applets "${TARGET_DIR}/usr/bin/${name}"
    done
    echo "post-build: installed COSMIC applet desktop files + multi-call symlinks"
else
    echo "post-build: cosmic-applets source/binary missing — panel plugins will NOT resolve"
fi

# --- COSMIC icon theme + per-app icons ---------------------------------------
# cosmic-panel resolves every panel/app icon through the freedesktop icon theme.
# Without a theme, buttons and dock entries render blank (the dock is an empty
# pill). Install the COSMIC icon theme (a valid
# theme that Inherits hicolor) plus every app's own com.system76.* icon into
# hicolor (each app carries its icon under res/ or resources/icons/).
COSMIC_ICONS_SRC="${COSMIC_SRC}/cosmic-icons"
if [ -d "${COSMIC_ICONS_SRC}/freedesktop/scalable" ]; then
    mkdir -p "${TARGET_DIR}/usr/share/icons/Cosmic"
    cp -a "${COSMIC_ICONS_SRC}/freedesktop/scalable" "${TARGET_DIR}/usr/share/icons/Cosmic/"
    # ...and the theme's extra/ set, which upstream's `just install` also installs
    # (plugged-into-power-symbolic for the power applet on desktops, and more).
    cp -a "${COSMIC_ICONS_SRC}/extra/scalable/." "${TARGET_DIR}/usr/share/icons/Cosmic/scalable/"
    cp -f "${COSMIC_ICONS_SRC}/index.theme" "${TARGET_DIR}/usr/share/icons/Cosmic/index.theme" 2>/dev/null || true
    # hicolor's index.theme comes from BR2_PACKAGE_HICOLOR_ICON_THEME. (A
    # hand-written one listed Directories= without per-directory sections, which
    # icon lookup treats as empty — every hicolor-only icon went missing.)
    [ -f "${TARGET_DIR}/usr/share/icons/hicolor/index.theme" ] ||
        echo "post-build: WARNING no hicolor index.theme — enable BR2_PACKAGE_HICOLOR_ICON_THEME"
    # cosmic-osd asks for system-restart-symbolic (restart confirm dialog), which
    # upstream gets from the Pop theme Cosmic inherits; we ship only
    # system-reboot-symbolic, so alias it.
    for v in "" "-symbolic"; do
        [ -e "${TARGET_DIR}/usr/share/icons/Cosmic/scalable/actions/system-restart${v}.svg" ] ||
            ln -sf "system-reboot${v}.svg" "${TARGET_DIR}/usr/share/icons/Cosmic/scalable/actions/system-restart${v}.svg"
    done
    mkdir -p "${TARGET_DIR}/usr/share/icons/hicolor/scalable/apps"
    # An app often ships one SVG per hicolor size (16x16 ... 256x256) under the
    # same name, and each copy overwrites the last: install them in a fixed
    # order (name, then size; scalable/ last) so the largest wins on every build
    # host. Plain `find` order is the filesystem's and differed between hosts.
    find "${COSMIC_SRC}" -iname 'com.system76.*.svg' -print 2>/dev/null |
        awk -F/ '{ n = $NF; size = 0
                   for (i = 1; i < NF; i++) {
                       if ($i == "scalable") size = 100000
                       else if ($i ~ /^[0-9]+x[0-9]+(@[0-9]+)?$/) { split($i, d, "x"); if (d[1] + 0 > size) size = d[1] + 0 }
                   }
                   printf "%s\t%06d\t%s\n", n, size, $0 }' |
        LC_ALL=C sort -t "$(printf '\t')" -k1,1 -k2,2n -k3,3 | cut -f3- |
        while IFS= read -r s; do
        [ -f "$s" ] || continue
        install -Dm0644 "$s" "${TARGET_DIR}/usr/share/icons/hicolor/scalable/apps/$(basename "$s")"
        # The shell actually renders with the `Cosmic` theme (not hicolor): put the
        # app icons there too, or the panel/dock fall back to the generic gear.
        install -Dm0644 "$s" "${TARGET_DIR}/usr/share/icons/Cosmic/scalable/apps/$(basename "$s")"
    done
    # The applets' own status icons (Bluetooth on/off, notifications / Do Not
    # Disturb, battery levels, …) live in each applet's data/icons tree under
    # names like cosmic-applet-bluetooth-active-symbolic — not com.system76.*, so
    # the loop above skipped them and those panel buttons were blank on the N100.
    # upstream's `just install` puts the trees into hicolor; so do we, plus the
    # apps' sized icon trees (res/icons/hicolor/<size>/apps).
    for t in "${COSMIC_SRC}"/cosmic-applets/*/data/icons \
             "${COSMIC_SRC}"/*/res/icons/hicolor \
             "${COSMIC_SRC}"/*/resources/icons/hicolor; do
        [ -d "$t" ] || continue
        cp -r "$t/." "${TARGET_DIR}/usr/share/icons/hicolor/"
    done
    echo "post-build: installed COSMIC icon theme + com.system76.* app icons (Cosmic + hicolor)"
else
    echo "post-build: ${COSMIC_ICONS_SRC} absent — icon theme NOT installed (panel/dock icons will be blank)"
fi

# --- COSMIC app .desktop files (apps + panel buttons resolve by app id) ------
# cosmic-panel's dock/panel buttons look each plugin up as <app-id>.desktop in
# the XDG data dirs; without these the button subprocess panics and the layer
# surface never attaches a buffer (the bar never appears). Install every entry.
# cosmic.desktop (cosmic-comp, cosmic-session) is a wayland-sessions entry, not an
# app: installed here it showed up in the App Library as "COSMIC" and launched a
# second compositor. examples/ is skipped for the same reason: libcosmic vendors
# iced, whose examples/todos ships iced-todos.desktop, and that put a "Todos - Iced"
# entry with no binary behind it in the App Library. The rm takes it out of build
# trees that already have it (post-build never deletes on its own).
find "${COSMIC_SRC}" \( -path '*/target/*' -o -path '*/examples/*' \) -prune -o -name '*.desktop' ! -name cosmic.desktop -print 2>/dev/null | while IFS= read -r d; do
    [ -f "$d" ] || continue
    install -Dm0644 "$d" "${TARGET_DIR}/usr/share/applications/$(basename "$d")"
 done
rm -f "${TARGET_DIR}/usr/share/applications/iced-todos.desktop"
echo "post-build: installed COSMIC app .desktop files"

# cosmic-panel's App button launches the App Library, whose binary is not part
# of this spike; ship a minimal .desktop so the button resolves (its absence
# made the button subprocess panic and exit).
if [ ! -e "${TARGET_DIR}/usr/share/applications/com.system76.CosmicAppLibrary.desktop" ]; then
    cat > "${TARGET_DIR}/usr/share/applications/com.system76.CosmicAppLibrary.desktop" <<'PENGYOS_APPLIB'
[Desktop Entry]
Type=Application
Name=App Library
Comment=COSMIC App Library
Exec=/usr/bin/cosmic-launcher
Icon=com.system76.CosmicLauncher
Terminal=false
Categories=COSMIC;
PENGYOS_APPLIB
    echo "post-build: added CosmicAppLibrary .desktop stub"
fi

# --- COSMIC dock: default favorites (the bottom launcher bar) ----------------
# cosmic-panel's dock app-list reads com.system76.CosmicAppList/v1/favorites, a
# RON list of app ids. It is empty by default, so the Dock renders as an empty
# pill. Seed it so the Dock shows a real launcher bar out of the box. Pengy
# (package/pengy, the Qt desktop app) leads when the image has it; the id is its
# .desktop name, which is also the app_id its window reports (so the running
# window shows on the pinned icon). PengyOS Updates closes the list, so checking
# for and installing updates is one click away.
PENGY_FAVORITE=""
[ -f "${TARGET_DIR}/usr/share/applications/pengy.desktop" ] && PENGY_FAVORITE='    "pengy",'
mkdir -p "${TARGET_DIR}/usr/share/cosmic/com.system76.CosmicAppList/v1"
cat > "${TARGET_DIR}/usr/share/cosmic/com.system76.CosmicAppList/v1/favorites" <<PENGYOS_FAVORITES
[
${PENGY_FAVORITE}
    "com.system76.CosmicTerm",
    "com.system76.CosmicFiles",
    "com.system76.CosmicEdit",
    "com.system76.CosmicStore",
    "com.system76.CosmicPlayer",
    "com.system76.CosmicSettings",
    "io.catbee.pengyos.Updates",
]
PENGYOS_FAVORITES
echo "post-build: seeded COSMIC dock favorites"

# --- App Library + Workspaces desktop entries --------------------------------
# cosmic-app-library is a separate upstream repo; its data lands with the binary
# above. The Workspaces overview is pop-os/cosmic-workspaces-epoch (binary
# cosmic-workspaces, installed with the COSMIC binaries; its .desktop comes in
# with the other COSMIC entries). Only when that is not built, fall back to a
# launcher-backed stub so the panel's workspaces button still resolves.
if [ -f "${COSMIC_SRC}/cosmic-app-library/data/com.system76.CosmicAppLibrary.desktop" ]; then
    install -Dm0644 "${COSMIC_SRC}/cosmic-app-library/data/com.system76.CosmicAppLibrary.desktop" \
        "${TARGET_DIR}/usr/share/applications/com.system76.CosmicAppLibrary.desktop"
    install -Dm0644 "${COSMIC_SRC}/cosmic-app-library/data/icons/com.system76.CosmicAppLibrary.svg" \
        "${TARGET_DIR}/usr/share/icons/Cosmic/scalable/apps/com.system76.CosmicAppLibrary.svg"
fi
if [ ! -e "${TARGET_DIR}/usr/share/applications/com.system76.CosmicWorkspaces.desktop" ]; then
    cat > "${TARGET_DIR}/usr/share/applications/com.system76.CosmicWorkspaces.desktop" <<'WSEOF'
[Desktop Entry]
Type=Application
Name=Workspaces
Comment=COSMIC Workspaces overview
Exec=/usr/bin/cosmic-launcher
Icon=com.system76.CosmicPanelWorkspacesButton
Terminal=false
NoDisplay=true
Categories=COSMIC;
WSEOF
fi
echo "post-build: installed App Library entry + Workspaces stub"

# --- host libraries the COSMIC apps need ---------------------------------------
# cosmic-store/cosmic-player/cosmic-app-library are host-built and link host
# libraries the image doesn't build (flatpak + ostree for store, gstreamer for
# player). Copy each binary's ldd closure into the target, skipping anything the
# image already provides. cosmic-store additionally needs an OpenSSL with ENGINE
# (its libcurl), which the Buildroot libcrypto lacks, so give store a PRIVATE
# /usr/lib/compat (openssl+curl) + a wrapper rather than touching the image's
# libcrypto (that would risk systemd/sshd).
for _b in cosmic-store cosmic-player cosmic-app-library; do
    [ -x "${TARGET_DIR}/usr/bin/${_b}" ] || continue
    ldd "${TARGET_DIR}/usr/bin/${_b}" 2>/dev/null | awk '/=> \//{print $3}' | while IFS= read -r _lib; do
        [ -n "${_lib}" ] || continue
        _base="$(basename "${_lib}")"
        [ -e "${TARGET_DIR}/usr/lib/${_base}" ] && continue
        cp -fL "${_lib}" "${TARGET_DIR}/usr/lib/${_base}" 2>/dev/null || true
    done
done
if [ -x "${TARGET_DIR}/usr/bin/cosmic-store" ]; then
    mkdir -p "${TARGET_DIR}/usr/lib/compat" "${TARGET_DIR}/usr/libexec"
    for _so in libcrypto.so.3 libssl.so.3 libcurl.so.4; do
        for _d in /usr/lib/x86_64-linux-gnu /usr/lib; do
            [ -e "${_d}/${_so}" ] && { cp -fL "${_d}/${_so}" "${TARGET_DIR}/usr/lib/compat/${_so}"; break; }
        done
    done
    mv -f "${TARGET_DIR}/usr/bin/cosmic-store" "${TARGET_DIR}/usr/libexec/cosmic-store.bin"
    cat > "${TARGET_DIR}/usr/bin/cosmic-store" <<'STOREEOF'
#!/bin/sh
LD_LIBRARY_PATH=/usr/lib/compat${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} exec /usr/libexec/cosmic-store.bin "$@"
STOREEOF
    chmod 755 "${TARGET_DIR}/usr/bin/cosmic-store"
fi
echo "post-build: copied COSMIC app host libs + store compat wrapper"

# --- Flatpak: CLI + system helper + Flathub (so cosmic-store can INSTALL) ----
# Flatpak is built by package/flatpak (CLI, system helper, portal, D-Bus and
# polkit files, install triggers). Images used to copy the build host's; clear
# the wrappers that left in TARGET_DIR (Buildroot never deletes target files).
rm -f "${TARGET_DIR}/usr/libexec/flatpak.bin" "${TARGET_DIR}/usr/libexec/flatpak-portal.bin"
# The Flatpak portal (org.freedesktop.portal.Flatpak): apps start
# sub-sandboxes through it. Chromium/Electron apps (Chrome, VS Code: zypak)
# and Steam (pressure-vessel) need it; without it Chrome died with "Failed
# to initialize (is the Flatpak D-Bus portal working?)", VS Code's windows
# crashed and Steam exited (71) (behoweather, 2026-09-30). Activate it by
# Exec=: the session bus is dbus-run-session's, so upstream's
# SystemdService= activation would never start it. It exits when idle.
if [ -x "${TARGET_DIR}/usr/libexec/flatpak-portal" ]; then
    printf '[D-BUS Service]\nName=org.freedesktop.portal.Flatpak\nExec=/usr/libexec/flatpak-portal\n' \
        > "${TARGET_DIR}/usr/share/dbus-1/services/org.freedesktop.portal.Flatpak.service"
    echo "post-build: flatpak portal D-Bus-activated by Exec"
else
    echo "post-build: flatpak-portal missing -- Chrome/VS Code/Steam Flatpaks will NOT start" >&2
fi
# The SYSTEM installation, the Flathub remote and its appstream can only be
# created by root (flatpak refuses EnsureRepo for a non-root build user), so
# they are a first-boot oneshot: etc/systemd/system/pengyos-flatpak-setup.service

# --- Mesa DRI driver aliases -------------------------------------------------
# Mesa's loader looks for <driver>_dri.so in ${libdir}/dri and dispatches to the
# megadriver's __driDriverGetExtensions_<driver>. Buildroot installs only the
# megadriver (libgallium-<ver>.so) with no /usr/lib/dri, so on real GPUs Mesa
# fell back to swrast (software GL, no dmabuf import) -> cosmic-comp could not
# composite the panel ("Dmabuf import extension not available"). Create the
# per-driver aliases as symlinks to the megadriver (exactly how distros ship it).
mkdir -p "${TARGET_DIR}/usr/lib/dri"
GALLIUM="$(cd "${TARGET_DIR}/usr/lib" && ls libgallium-*.so 2>/dev/null | head -1)"
if [ -n "${GALLIUM}" ]; then
    for d in iris crocus i915 radeonsi nouveau virtio_gpu swrast kms_swrast softpipe llvmpipe zink v3d vc4; do
        ln -sf "../${GALLIUM}" "${TARGET_DIR}/usr/lib/dri/${d}_dri.so"
    done
    echo "post-build: created /usr/lib/dri/*_dri.so -> ${GALLIUM}"
fi

# --- Clean up files the old pengyos-desktop-av overlay left in TARGET_DIR -------
# (Buildroot never deletes target files.) Its wireplumber.conf was invalid for
# WirePlumber 0.5 ("section 'global.policy.alsa' has no value" -> wireplumber
# crash-looped -> no audio devices); its /etc/group carried comment lines.
# Its user wireplumber.service and system bluetooth.service replaced the
# packages' own units — `make wireplumber-reinstall bluez5_utils-reinstall`
# restores those on an existing output dir.
rm -f "${TARGET_DIR}/etc/xdg/wireplumber/wireplumber.conf"
sed -i '/^#/d' "${TARGET_DIR}/etc/group"
# Mesa used to name the radv manifest radeon_icd..json (see external.mk); an
# output dir built before that still has it next to radeon_icd.json, and the
# Vulkan loader would then list every AMD GPU twice.
rm -f "${TARGET_DIR}/usr/share/vulkan/icd.d/radeon_icd..json"
# VA-API: libva looks for drivers in /usr/lib/va (where Intel's iHD installs), but
# Mesa's Gallium VA drivers (radeonsi for AMD, …) install to /usr/lib/dri — link
# them in so AMD hardware video decode is found too.
mkdir -p "${TARGET_DIR}/usr/lib/va"
for d in "${TARGET_DIR}"/usr/lib/dri/*_drv_video.so; do
    [ -e "$d" ] && ln -sf "../dri/${d##*/}" "${TARGET_DIR}/usr/lib/va/${d##*/}"
done

# The session ran as cosmic-session.service (auto-login only) until greetd took
# over (2026-09-30). An output dir built before that still has the unit, its
# drop-ins and its enable link from the old overlay; left there it races
# greetd for tty1 (logind: VirtualTerminalAlreadyTaken, no /run/user/UID).
rm -rf "${TARGET_DIR}/etc/systemd/system/cosmic-session.service" \
       "${TARGET_DIR}/etc/systemd/system/cosmic-session.service.d" \
       "${TARGET_DIR}/etc/systemd/system/multi-user.target.wants/cosmic-session.service"

# --- COSMIC data that `just install` puts in place --------------------------
# Only the binaries are copied above; each component's justfile also installs
# D-Bus/polkit data. Missing ones found in behoweather's journal (2026-09-30):
# - the tray: cosmic-applet-status-area --status-notifier-watcher, D-Bus-
#   activated as com.system76.CosmicStatusNotifierWatcher ("was not provided
#   by any .service files": no tray icons for Steam, Discord, ...);
# - the network applet's NetworkManager secret agent (Wi-Fi password prompts);
# - cosmic-greeter-daemon's bus policy: the login screen asks it (as root) for
#   each user's wallpaper, avatar and theme; without it the screen was grey.
#
# The watcher is TWO files and upstream's `_install_status_notifier_watcher`
# installs both. The dbus-1/services file is only a pointer: it ends with
# `SystemdService=com.system76.CosmicStatusNotifierWatcher.service`, and the
# session bus is `dbus-daemon --session --systemd-activation`, so activation is
# answered by systemd --user, which resolves that name as a *unit* — a file in
# /usr/share/dbus-1/services is not one. The pointer without the unit fails at
# runtime: `Unit com.system76.CosmicStatusNotifierWatcher.service not found`, the
# panel logs "Failed to start status notifier watcher" / "Failed to register
# status notifier host", no StatusNotifierHost exists, and every tray icon
# (Discord, Steam, SyncThingy, …) is silently absent. behoweather showed exactly
# that on 26.10.14 (2026-10-08): the 2026-09-30 "no tray icons" fix had shipped
# half the pair. check-dbus-units.py (below) now fails the build on any of them.
CA_SRC="${COSMIC_SRC}/cosmic-applets"
if [ -d "${CA_SRC}" ]; then
    sed "s|@bindir@|/usr/bin|" "${CA_SRC}/cosmic-applet-status-area/data/dbus-1/com.system76.CosmicStatusNotifierWatcher.service.in" \
        > "${TARGET_DIR}/usr/share/dbus-1/services/com.system76.CosmicStatusNotifierWatcher.service"
    mkdir -p "${TARGET_DIR}/usr/lib/systemd/user"
    sed "s|@bindir@|/usr/bin|" "${CA_SRC}/cosmic-applet-status-area/data/com.system76.CosmicStatusNotifierWatcher.service.in" \
        > "${TARGET_DIR}/usr/lib/systemd/user/com.system76.CosmicStatusNotifierWatcher.service"
    echo "post-build: COSMIC tray watcher installed as both a dbus service and its systemd user unit"
    install -Dm0644 "${CA_SRC}/cosmic-applet-network/data/dbus-1/system.d/com.system76.CosmicSettings.Applet.NetworkManager.SecretAgent.conf" \
        "${TARGET_DIR}/usr/share/dbus-1/system.d/com.system76.CosmicSettings.Applet.NetworkManager.SecretAgent.conf"
fi
CG_SRC="${COSMIC_SRC}/cosmic-greeter"
if [ -f "${CG_SRC}/dbus/com.system76.CosmicGreeter.conf" ]; then
    install -Dm0644 "${CG_SRC}/dbus/com.system76.CosmicGreeter.conf" \
        "${TARGET_DIR}/usr/share/dbus-1/system.d/com.system76.CosmicGreeter.conf"
fi

# --- polkit's agent helper where COSMIC looks for it --------------------------
# cosmic-osd (COSMIC's polkit agent: the "authentication required" dialog)
# runs /usr/libexec/polkit-agent-helper-1; Buildroot's polkit installs it in
# /usr/lib/polkit-1. Every admin prompt failed ("failed to create helper,
# entity not found", 9x on behoweather). The link keeps the setuid binary.
ln -sf ../lib/polkit-1/polkit-agent-helper-1 "${TARGET_DIR}/usr/libexec/polkit-agent-helper-1"

# --- Login screen sessions ----------------------------------------------------
# The greeter's state (runs as cosmic-greeter; upstream's tmpfiles).
mkdir -p "${TARGET_DIR}/usr/lib/tmpfiles.d"
cat > "${TARGET_DIR}/usr/lib/tmpfiles.d/cosmic-greeter.conf" <<'TMPF'
d /var/lib/cosmic-greeter 0750 cosmic-greeter cosmic-greeter -
d /run/cosmic-greeter     0755 cosmic-greeter cosmic-greeter -
TMPF

# --- ALSA -> PipeWire ----------------------------------------------------------
# PipeWire installs its ALSA plugin config in /usr/share/alsa/alsa.conf.d, but
# alsa-lib only reads /etc/alsa/conf.d (and /var/lib, /usr/etc); distros link
# the files in. Without them ALSA's "default" was the card itself: GStreamer
# apps (COSMIC Player) played to the ALC897 analog jack while PipeWire's
# default was the monitor -- no sound (behoweather, 2026-09-30). Firefox talks
# to PipeWire directly and was fine.
mkdir -p "${TARGET_DIR}/etc/alsa/conf.d"
for f in 50-pipewire.conf 99-pipewire-default.conf; do
    [ -e "${TARGET_DIR}/usr/share/alsa/alsa.conf.d/$f" ] && \
        ln -sf "/usr/share/alsa/alsa.conf.d/$f" "${TARGET_DIR}/etc/alsa/conf.d/$f"
done
ls "${TARGET_DIR}/etc/alsa/conf.d/99-pipewire-default.conf" >/dev/null

# --- Compose / dead keys: the X11 locale data -------------------------------
# libxkbcommon (every COSMIC app) and Xlib find the Compose table through
# /usr/share/X11/locale/compose.dir, which maps every UTF-8 locale to
# en_US.UTF-8/Compose. Buildroot's locale purge (whitelist "C en_US") deletes
# en_US.UTF-8/ and also every *file* there (compose.dir, locale.alias), so
# dead keys and Compose sequences did nothing ("couldn't find a Compose file").
# The purge runs before post-build scripts; restore from staging.
X11_LOCALE="${STAGING_DIR}/usr/share/X11/locale"
# Every UTF-8 directory, not just en_US: since 26.10.23 the image generates other
# locales (desktop.config), and el_GR, fi_FI, pt_BR, ru_RU and the CJK locales
# have Compose files of their own (compose.dir). 16 directories, ~0.8 MB.
if [ -f "${X11_LOCALE}/compose.dir" ]; then
    cp -a "${X11_LOCALE}"/*.UTF-8 "${X11_LOCALE}/compose.dir" "${X11_LOCALE}/locale.alias" \
        "${X11_LOCALE}/locale.dir" "${TARGET_DIR}/usr/share/X11/locale/"
    echo "post-build: restored the X11 Compose data (UTF-8 locales)"
fi

# --- cups-browsed: keep its network-printer queues at shutdown ----------------
# Deleting them on the way down stalled a reboot for 90 s (behoweather,
# 2026-09-30; the overlay also caps its stop at 10 s: cups-browsed.service.d).
# They are recreated/updated from DNS-SD at the next start anyway.
if [ -f "${TARGET_DIR}/etc/cups/cups-browsed.conf" ]; then
    grep -q '^KeepGeneratedQueuesOnShutdown' "${TARGET_DIR}/etc/cups/cups-browsed.conf" || \
        echo 'KeepGeneratedQueuesOnShutdown Yes' >> "${TARGET_DIR}/etc/cups/cups-browsed.conf"
fi
# Its recipe installs the unit 0755; systemd warned "is marked executable"
# three times per boot (behoweather, 26.10.20).
[ ! -f "${TARGET_DIR}/usr/lib/systemd/system/cups-browsed.service" ] || \
    chmod 0644 "${TARGET_DIR}/usr/lib/systemd/system/cups-browsed.service"

# --- Files earlier images shipped -------------------------------------------------
# Removed from the overlay in 26.10.23 (tools/pengyos-desktop-net, retired: none
# of them was read by anything). Buildroot never deletes from target/, so an
# existing build tree would keep shipping them.
rm -f "${TARGET_DIR}/etc/NetworkManager/conf.d/10-pengyos.conf" \
      "${TARGET_DIR}/etc/skel/.config/cosmic-settings/network.toml" \
      "${TARGET_DIR}/etc/skel/.config/cosmic/panel.toml" \
      "${TARGET_DIR}/etc/wpa_supplicant.conf" \
      "${TARGET_DIR}/wifi-firmware.txt"
rmdir "${TARGET_DIR}/etc/skel/.config/cosmic-settings" 2>/dev/null || true
# The "Printers" launcher that opened CUPS' web pages, replaced by the Printers
# app (io.catbee.pengyos.Printers.desktop) on 2026-10-12.
rm -f "${TARGET_DIR}/usr/share/applications/pengyos-printers.desktop"

# --- Host names: .local through avahi first (nss-mdns) -------------------------
# One mDNS stack, avahi (resolved.conf.d sets MulticastDNS=no). systemd's recipe
# puts "resolve [!UNAVAIL=return]" first in hosts:, and resolved answers every
# name, so the mdns4_minimal entry nss-mdns adds after it was never reached and
# resolved had to keep its own mDNS stack running next to avahi's ("Detected
# another IPv4 mDNS stack", every boot). Fedora's order: files, then .local via
# avahi, then resolved for DNS.
if grep -q '^hosts:' "${TARGET_DIR}/etc/nsswitch.conf" && \
   [ -e "${TARGET_DIR}/usr/lib/libnss_mdns4_minimal.so.2" ]; then
    sed -i 's/^hosts:.*/hosts:          files myhostname mdns4_minimal [NOTFOUND=return] resolve [!UNAVAIL=return] dns/' \
        "${TARGET_DIR}/etc/nsswitch.conf"
fi

# --- One printer launcher --------------------------------------------------------
# CUPS ships cups.desktop ("Manage Printing", opens localhost:631); the Printers
# app (io.catbee.pengyos.Printers.desktop) does that job.
rm -f "${TARGET_DIR}/usr/share/applications/cups.desktop"

# --- CUPS administration: the wheel group --------------------------------------
# The desktop user is in wheel (the installer adds it; sudo too); CUPS only let
# lpadmin/sys/root add printers or change defaults. With wheel in SystemGroup the
# Printers app (and lpadmin) administer CUPS over its local socket by peer
# credentials, with no password; CUPS' web pages still ask for one (CUPS is
# built without PAM and checks it against /etc/shadow itself).
if [ -f "${TARGET_DIR}/etc/cups/cups-files.conf" ]; then
    sed -i -E 's/^SystemGroup .*/SystemGroup lpadmin sys root wheel/' "${TARGET_DIR}/etc/cups/cups-files.conf"
    grep -q '^SystemGroup .*wheel' "${TARGET_DIR}/etc/cups/cups-files.conf"
    echo "post-build: CUPS admin group: wheel"
fi

# --- Icons COSMIC asks the theme for -------------------------------------------
# A COSMIC button whose icon is missing from every theme renders as a blank
# square, with no error anywhere. COSMIC's theme inherits Pop for these (they
# exist only in Pop's symbolic set, installed by pop-cursor-theme); until
# 26.10.25 only Pop's cursors were installed, and the Store's release-notes
# button, Files' grid/list toggle, the VPN icon, Player's repeat/subtitle
# buttons, About pages' links and two Settings page icons were all blank. The
# list is every name the pinned COSMIC sources request by literal that no
# app embeds (audited 2026-10-12); fail the build if one goes missing again.
for icon in help-info-symbolic link-symbolic view-grid-symbolic view-list-symbolic \
            network-vpn-symbolic media-playlist-no-repeat-symbolic \
            media-playlist-repeat-song-symbolic media-view-subtitles-symbolic \
            preferences-desktop-display-symbolic preferences-system-time-symbolic \
            touchpad-disabled-symbolic; do
    if ! find "${TARGET_DIR}/usr/share/icons/Cosmic" "${TARGET_DIR}/usr/share/icons/Pop" \
              "${TARGET_DIR}/usr/share/icons/hicolor" -name "${icon}.svg" 2>/dev/null | grep -q .; then
        echo "post-build: ERROR: icon ${icon} is in no installed theme (COSMIC draws a blank button)" >&2
        exit 1
    fi
done
echo "post-build: COSMIC's theme icons are all present"

# --- The MIME index: which installed app opens which file type ------------------
# (update-desktop-database -> /usr/share/applications/mimeinfo.cache.) Without
# it, "Open with" lists stayed empty ("No registered applications"); the
# defaults themselves are in the overlay's mimeapps.list. Buildroot's own
# host-desktop-file-utils (a dependency of desktop-file-utils-target) builds it:
# a build host without update-desktop-database (a fresh distrobox) silently
# shipped an image with no index.
UDD="${HOST_DIR}/bin/update-desktop-database"
[ -x "${UDD}" ] || { echo "post-build: ${UDD} missing (host-desktop-file-utils)" >&2; exit 1; }
"${UDD}" -q "${TARGET_DIR}/usr/share/applications"
echo "post-build: MIME index built (mimeinfo.cache)"

# --- No group/other-writable files under /usr ----------------------------------
# Data copied from the COSMIC checkouts with cp -a keeps the build host's modes
# (664/775 under a umask-002 account, 644/755 elsewhere), so two hosts built
# different images. Nothing under /usr is meant to be writable by others.
find "${TARGET_DIR}/usr" -perm /022 ! -type l -exec chmod go-w {} +

# --- Every dbus-activated service must name a unit the image ships -------------
# See board/common/check-dbus-units.py: with --systemd-activation on both buses,
# a SystemdService= that resolves to no unit is a feature that quietly never
# starts (the COSMIC tray on behoweather 26.10.14).
python3 "$(dirname "$0")/../common/check-dbus-units.py" "${TARGET_DIR}"

# --- Last: every shared library the image needs is in it ------------------------
# (the edition's later post-build runs this again over the finished tree)
python3 "$(dirname "$0")/../common/check-needed-libs.py" "${TARGET_DIR}"
