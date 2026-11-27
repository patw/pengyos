################################################################################
#
# novnc
#
################################################################################

NOVNC_VERSION = 1.6.0
NOVNC_SITE = $(call github,novnc,noVNC,v$(NOVNC_VERSION))
NOVNC_LICENSE = MPL-2.0 (core), MIT (vendor/pako)
NOVNC_LICENSE_FILES = LICENSE.txt docs/LICENSE.MPL-2.0 vendor/pako/LICENSE

# Only the library: core/ (the RFB client, ES modules) and the pako zlib it
# imports. pengy-vmm serves them at /novnc/ and draws its own small viewer page,
# so noVNC's app/ (vnc.html, its CSS and images) is not installed.
define NOVNC_INSTALL_TARGET_CMDS
	$(INSTALL) -d $(TARGET_DIR)/usr/share/novnc
	cp -a $(@D)/core $(TARGET_DIR)/usr/share/novnc/
	$(INSTALL) -d $(TARGET_DIR)/usr/share/novnc/vendor
	cp -a $(@D)/vendor/pako $(TARGET_DIR)/usr/share/novnc/vendor/
	$(INSTALL) -m 0644 $(@D)/LICENSE.txt $(@D)/docs/LICENSE.MPL-2.0 \
		$(TARGET_DIR)/usr/share/novnc/
endef

$(eval $(generic-package))
