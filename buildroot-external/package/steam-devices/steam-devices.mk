################################################################################
#
# steam-devices
#
################################################################################

STEAM_DEVICES_VERSION = 1.0.0.61
STEAM_DEVICES_SOURCE = v$(STEAM_DEVICES_VERSION).tar.gz
STEAM_DEVICES_SITE = https://github.com/ValveSoftware/steam-devices/archive/refs/tags
STEAM_DEVICES_LICENSE = MIT
STEAM_DEVICES_LICENSE_FILES = LICENSE

define STEAM_DEVICES_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 -t $(TARGET_DIR)/usr/lib/udev/rules.d \
		$(@D)/60-steam-input.rules $(@D)/60-steam-vr.rules
	# uinput is a module here: load it at boot so /dev/uinput exists for the
	# rules' uaccess tag when Steam starts.
	mkdir -p $(TARGET_DIR)/usr/lib/modules-load.d
	echo uinput > $(TARGET_DIR)/usr/lib/modules-load.d/steam-uinput.conf
endef

$(eval $(generic-package))
