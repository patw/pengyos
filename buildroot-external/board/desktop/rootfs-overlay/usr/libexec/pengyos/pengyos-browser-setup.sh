#!/bin/sh
# PengyOS Desktop - install the web browser (Firefox, from Flathub) once.
#
# Every mainstream desktop ships a browser; without one, links, the Printers
# page and "Open With" for web addresses had nothing to open them. Firefox is
# what Pop!_OS, Fedora and Ubuntu ship. It is a Flatpak (the app lane), so it
# needs the network: until it succeeds, this fails and runs again (the service
# retries, and every boot until done).
set -e

MARK=/var/lib/flatpak/.pengyos-browser
[ -e "$MARK" ] && exit 0

# Flathub, in case the first-boot setup ran offline (its remote-add is
# best-effort).
/usr/bin/flatpak --system remote-add --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo

/usr/bin/flatpak --system install --noninteractive -y flathub org.mozilla.firefox

touch "$MARK"
echo "pengyos-browser-setup: Firefox installed"
