################################################################################
#
# xdg-desktop-portal
#
################################################################################

XDG_DESKTOP_PORTAL_VERSION = 1.22.1
XDG_DESKTOP_PORTAL_SOURCE = $(XDG_DESKTOP_PORTAL_VERSION).tar.gz
XDG_DESKTOP_PORTAL_SITE = https://github.com/flatpak/xdg-desktop-portal/archive/refs/tags
XDG_DESKTOP_PORTAL_LICENSE = LGPL-2.1-only
XDG_DESKTOP_PORTAL_LICENSE_FILES = LICENSE

$(eval $(meson-package))
