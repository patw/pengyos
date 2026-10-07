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

# The portal ships its own icons (the screenshot dialog's selection / window /
# screen mode buttons); upstream's justfile installs data/icons into hicolor, but
# cargo-package installs only the binary -- the three buttons were blank.
define COSMIC_PORTAL_INSTALL_ICONS
	cd $(@D)/data/icons && find . -type f -exec \
		$(INSTALL) -D -m 0644 {} $(TARGET_DIR)/usr/share/icons/hicolor/{} \;
endef
COSMIC_PORTAL_POST_INSTALL_TARGET_HOOKS += COSMIC_PORTAL_INSTALL_ICONS

$(eval $(cargo-package))
