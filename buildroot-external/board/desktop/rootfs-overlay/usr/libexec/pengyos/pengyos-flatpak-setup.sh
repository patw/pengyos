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

# Flathub remote (this also initialises the repo).
/usr/bin/flatpak --system remote-add --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo || true

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
