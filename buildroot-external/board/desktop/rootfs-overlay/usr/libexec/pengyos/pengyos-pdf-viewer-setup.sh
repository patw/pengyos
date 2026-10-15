#!/bin/sh
# PengyOS Desktop - install the PDF viewer (Evince, from Flathub) once.
#
# Until now the only handler for a PDF was Firefox: the overlay's mimeapps.list
# named it, so a PDF opened in a browser. That works, and it is the fallback
# kept below, but it is not a document viewer - no page navigation, no
# continuous scroll, no reflow, no printing a page range, and a 200-page
# document is a browser tab.
#
# Evince is the human-ratified entry for the PDF category in
# tools/pengyos-desktop-curation/curated.toml (spec Principle 8: the default-app
# set is a human decision, never an AI one). This wires that ratified entry into
# the build; it does not choose it.
#
# It is a Flatpak (the app lane), so it needs the network: until it succeeds,
# this fails and runs again (the service retries, and the NetworkManager
# dispatcher starts it once a connection is up) - exactly the Firefox pattern.
#
# The Flatpak carries its own poppler (the manifest builds poppler with the GTK
# backend), so the image's host poppler - which Buildroot 2026.08 builds without
# the glib wrapper - is not involved. It declares --socket=wayland, so it draws
# natively under cosmic-comp.
set -e

MARK=/var/lib/flatpak/.pengyos-pdf-viewer
[ -e "$MARK" ] && exit 0

# Flathub, in case the first-boot setup ran offline (its remote-add is
# best-effort).
/usr/bin/flatpak --system remote-add --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo

/usr/bin/flatpak --system install --noninteractive -y flathub org.gnome.Evince

touch "$MARK"
echo "pengyos-pdf-viewer-setup: Evince installed"
