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

$(eval $(meson-package))
