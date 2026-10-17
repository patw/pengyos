################################################################################
#
# switcheroo-control
#
################################################################################

SWITCHEROO_CONTROL_VERSION = 3.0
SWITCHEROO_CONTROL_SITE = https://gitlab.freedesktop.org/hadess/switcheroo-control/-/archive/$(SWITCHEROO_CONTROL_VERSION)
SWITCHEROO_CONTROL_LICENSE = GPL-3.0
SWITCHEROO_CONTROL_LICENSE_FILES = COPYING
SWITCHEROO_CONTROL_DEPENDENCIES = host-pkgconf libglib2 libgudev libdrm
SWITCHEROO_CONTROL_CONF_OPTS = \
	-Dsystemdsystemunitdir=/usr/lib/systemd/system \
	-Dhwdbdir=/usr/lib/udev/hwdb.d \
	-Drulesdir=/usr/lib/udev/rules.d \
	-Dgtk_doc=false \
	-Dtests=false

# meson substitutes the python3 it found on the build host into switcherooctl's
# shebang (output/<target>/host/bin/python3), so on a machine the tool died with
# "required file not found". The daemon is C and was unaffected. The target's
# python3 and gi come with the Desktop (pengyos-updates and friends need them).
define SWITCHEROO_CONTROL_FIX_SHEBANG
	$(SED) '1s|^#!.*python3.*$$|#!/usr/bin/python3|' $(TARGET_DIR)/usr/bin/switcherooctl
endef
SWITCHEROO_CONTROL_POST_INSTALL_TARGET_HOOKS += SWITCHEROO_CONTROL_FIX_SHEBANG

$(eval $(meson-package))
