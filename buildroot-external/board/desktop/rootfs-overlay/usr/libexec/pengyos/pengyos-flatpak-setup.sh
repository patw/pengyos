#!/bin/sh
# PengyOS Desktop - one-time Flatpak setup (first boot, runs as root).
#
# The image ships the flatpak CLI + system helper + D-Bus/polkit glue, but the
# system installation itself (repo + Flathub remote + appstream catalogue) can
# only be created by root -- flatpak refuses "EnsureRepo" for the non-root
# build user in post-build. So do it here, once, on first boot.
set -e

MARK=/var/lib/flatpak/.pengyos-setup
[ -e "$MARK" ] && exit 0

mkdir -p /var/lib/flatpak

# Flathub remote (this also initialises the repo). Offline this fails, and so
# must the script: it used to carry on and write the marker, so a machine
# installed before it joined a network never got a system Flathub, the Store
# listed no sources, and anything added by hand became a per-user remote
# (a laptop on 26.10.27, 2026-10-13). The service retries, and
# NetworkManager's dispatcher (50-pengyos-flatpak-setup) starts it again the
# moment a connection reaches the internet.
/usr/bin/flatpak --system remote-add --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo

# flatpak's 500 MB default min-free-space blocks appstream/app pulls on this
# ~1.8 GB root; lower it.
CFG=/var/lib/flatpak/repo/config
if [ -f "$CFG" ]; then
    if grep -q '^min-free-space-size=' "$CFG"; then
        sed -i 's/^min-free-space-size=.*/min-free-space-size=50MB/' "$CFG"
    else
        sed -i '/^\[core\]/a min-free-space-size=50MB' "$CFG"
    fi
fi

# Pre-fetch the appstream catalogue the store's Explore view reads.
/usr/bin/flatpak --system update --appstream || true

touch "$MARK"
echo "pengyos-flatpak-setup: Flathub remote + appstream ready"
