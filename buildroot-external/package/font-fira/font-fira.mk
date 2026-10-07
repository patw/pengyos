################################################################################
#
# font-fira
#
################################################################################

FONT_FIRA_VERSION = 4.202
FONT_FIRA_SITE = $(call github,mozilla,Fira,$(FONT_FIRA_VERSION))
FONT_FIRA_LICENSE = OFL-1.1
FONT_FIRA_LICENSE_FILES = LICENSE

# Fira Sans (regular width, every weight + italic) and Fira Mono; the
# Condensed / Compressed families are left out.
define FONT_FIRA_INSTALL_TARGET_CMDS
	$(INSTALL) -d $(TARGET_DIR)/usr/share/fonts/fira
	$(INSTALL) -m 0644 $(@D)/otf/FiraSans-*.otf $(@D)/otf/FiraMono-*.otf \
		$(TARGET_DIR)/usr/share/fonts/fira/
endef

$(eval $(generic-package))
