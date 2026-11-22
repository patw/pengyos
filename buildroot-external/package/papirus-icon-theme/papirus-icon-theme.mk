################################################################################
#
# papirus-icon-theme
#
################################################################################

# Keep the version and sha256 in step with tools/pengyos-themes/catalogue: the
# catalogue lists the same pack, and `pengyos-themes icons list` shows it as
# coming with PengyOS only while every theme it names is here.
PAPIRUS_ICON_THEME_VERSION = 20260801
PAPIRUS_ICON_THEME_SOURCE = $(PAPIRUS_ICON_THEME_VERSION).tar.gz
PAPIRUS_ICON_THEME_SITE = https://github.com/PapirusDevelopmentTeam/papirus-icon-theme/archive/refs/tags
PAPIRUS_ICON_THEME_LICENSE = GPL-3.0
PAPIRUS_ICON_THEME_LICENSE_FILES = LICENSE

# Papirus-Dark and Papirus-Light are mostly symlinks into Papirus
# (Papirus-Dark/128x128 -> ../Papirus/128x128), so all three go in together,
# copied with their links as they are.
PAPIRUS_ICON_THEME_THEMES = Papirus Papirus-Dark Papirus-Light

define PAPIRUS_ICON_THEME_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/share/icons
	for t in $(PAPIRUS_ICON_THEME_THEMES); do \
		rm -rf $(TARGET_DIR)/usr/share/icons/$$t; \
		cp -a $(@D)/$$t $(TARGET_DIR)/usr/share/icons/$$t || exit 1; \
	done
endef

$(eval $(generic-package))
