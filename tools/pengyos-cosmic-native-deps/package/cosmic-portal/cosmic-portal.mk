################################################################################
#
# cosmic-portal
#
################################################################################

COSMIC_PORTAL_VERSION = epoch-1.8.0
COSMIC_PORTAL_SOURCE = $(COSMIC_PORTAL_VERSION).tar.gz
COSMIC_PORTAL_SITE = https://github.com/pop-os/xdg-desktop-portal-cosmic/archive/refs/tags
COSMIC_PORTAL_LICENSE = GPL-3.0-or-later
COSMIC_PORTAL_LICENSE_FILES = LICENSE

$(eval $(cargo-package))
