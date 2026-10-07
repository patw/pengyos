################################################################################
#
# cosmic-sound-theme
#
################################################################################

COSMIC_SOUND_THEME_VERSION = epoch-1.9.0
COSMIC_SOUND_THEME_SITE = $(call github,pop-os,cosmic-sound-theme,$(COSMIC_SOUND_THEME_VERSION))
COSMIC_SOUND_THEME_LICENSE = CC-BY-SA-4.0
COSMIC_SOUND_THEME_LICENSE_FILES = LICENSE

# COSMIC 1.9 plays its battery sounds from a theme named "Pop" (hardcoded in
# cosmic-settings-daemon); this theme is COSMIC's fork of it.
define COSMIC_SOUND_THEME_POP_ALIAS
	ln -sfn COSMIC $(TARGET_DIR)/usr/share/sounds/Pop
endef
COSMIC_SOUND_THEME_POST_INSTALL_TARGET_HOOKS += COSMIC_SOUND_THEME_POP_ALIAS

$(eval $(meson-package))
