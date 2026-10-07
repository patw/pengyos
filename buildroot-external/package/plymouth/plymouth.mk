################################################################################
#
# plymouth
#
################################################################################

PLYMOUTH_VERSION = 26.134.222
PLYMOUTH_SITE = https://gitlab.freedesktop.org/plymouth/plymouth/-/archive/$(PLYMOUTH_VERSION)
PLYMOUTH_LICENSE = GPL-2.0+
PLYMOUTH_LICENSE_FILES = COPYING
PLYMOUTH_INSTALL_STAGING = YES
PLYMOUTH_DEPENDENCIES = host-pkgconf host-gettext libpng libdrm libevdev \
	libxkbcommon xkeyboard-config freetype systemd udev

# No pango/cairo (freetype renders the few labels), no GTK (X11 preview
# plugin), no upstart; release info comes from os-release.
PLYMOUTH_CONF_OPTS = \
	-Dsystemd-integration=true \
	-Dudev=enabled \
	-Ddrm=true \
	-Dfreetype=enabled \
	-Dpango=disabled \
	-Dgtk=disabled \
	-Dupstart-monitoring=false \
	-Ddocs=false \
	-Drelease-file=/etc/os-release \
	-Dboot-tty=/dev/tty1

define PLYMOUTH_INSTALL_THEME
	mkdir -p $(TARGET_DIR)/usr/share/plymouth/themes/pengyos
	cp $(TARGET_DIR)/usr/share/plymouth/themes/spinner/*.png \
		$(TARGET_DIR)/usr/share/plymouth/themes/pengyos/
	$(INSTALL) -m 0644 $(PLYMOUTH_PKGDIR)/pengyos-theme/pengyos.plymouth \
		$(PLYMOUTH_PKGDIR)/pengyos-theme/watermark.png \
		$(TARGET_DIR)/usr/share/plymouth/themes/pengyos/
	$(INSTALL) -D -m 0644 $(PLYMOUTH_PKGDIR)/plymouthd.conf \
		$(TARGET_DIR)/etc/plymouth/plymouthd.conf
endef
PLYMOUTH_POST_INSTALL_TARGET_HOOKS += PLYMOUTH_INSTALL_THEME

$(eval $(meson-package))
