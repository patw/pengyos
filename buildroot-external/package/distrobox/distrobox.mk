################################################################################
#
# distrobox
#
################################################################################

DISTROBOX_VERSION = 1.8.2.5
DISTROBOX_SITE = $(call github,89luca89,distrobox,$(DISTROBOX_VERSION))
DISTROBOX_LICENSE = GPL-3.0
DISTROBOX_LICENSE_FILES = COPYING.md

# Shell scripts only; upstream's installer copies the local files (no download
# when run from the source tree).
define DISTROBOX_INSTALL_TARGET_CMDS
	cd $(@D) && ./install --prefix $(TARGET_DIR)/usr
endef

$(eval $(generic-package))
