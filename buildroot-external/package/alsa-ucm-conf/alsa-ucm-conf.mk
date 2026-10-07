################################################################################
#
# alsa-ucm-conf
#
################################################################################

# Version tracks alsa-lib (1.2.16.1 in Buildroot 2026.08). Pure data package:
# the ucm2 tree is read at runtime, there is nothing to build.
ALSA_UCM_CONF_VERSION = 1.2.16.1
ALSA_UCM_CONF_SITE = $(call github,alsa-project,alsa-ucm-conf,v$(ALSA_UCM_CONF_VERSION))
ALSA_UCM_CONF_LICENSE = BSD-3-Clause
ALSA_UCM_CONF_LICENSE_FILES = LICENSE
ALSA_UCM_CONF_DEPENDENCIES = alsa-lib

define ALSA_UCM_CONF_INSTALL_TARGET_CMDS
	$(INSTALL) -d $(TARGET_DIR)/usr/share/alsa/ucm2
	cp -a $(@D)/ucm2/. $(TARGET_DIR)/usr/share/alsa/ucm2/
	$(INSTALL) -d $(TARGET_DIR)/usr/share/alsa/ucm
	cp -a $(@D)/ucm/. $(TARGET_DIR)/usr/share/alsa/ucm/
endef

$(eval $(generic-package))
