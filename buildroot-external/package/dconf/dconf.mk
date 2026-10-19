################################################################################
#
# dconf
#
################################################################################

DCONF_VERSION = 51.0
DCONF_SOURCE = dconf-$(DCONF_VERSION).tar.xz
DCONF_SITE = https://download.gnome.org/sources/dconf/$(firstword $(subst ., ,$(DCONF_VERSION)))
DCONF_LICENSE = LGPL-2.1+
DCONF_LICENSE_FILES = COPYING
DCONF_INSTALL_STAGING = YES
DCONF_DEPENDENCIES = host-pkgconf libglib2 dbus

# dconf-service is D-Bus activated through the systemd user unit
# (SystemdService=dconf.service), so the unit has to land in the image.
DCONF_CONF_OPTS = \
	-Dbash_completion=false \
	-Dman=false \
	-Dgtk_doc=false \
	-Dvapi=false \
	-Dsystemduserunitdir=/usr/lib/systemd/user

# Before this recipe, board/desktop/post-build.sh copied the build host's
# libdconf.so.1 into the target as a regular file (the ldd closure of the
# host-built COSMIC apps). meson refuses to install its symlink over it, so a
# tree built before dconf fails here. Post-build skips libraries the image
# already has, so a fresh tree never gets the copy.
define DCONF_REMOVE_HOST_COPY
	if [ -f $(TARGET_DIR)/usr/lib/libdconf.so.1 ] && [ ! -L $(TARGET_DIR)/usr/lib/libdconf.so.1 ]; then \
		rm -f $(TARGET_DIR)/usr/lib/libdconf.so.1; \
	fi
endef
DCONF_PRE_INSTALL_TARGET_HOOKS += DCONF_REMOVE_HOST_COPY

$(eval $(meson-package))
