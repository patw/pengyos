################################################################################
#
# flatpak
#
################################################################################

FLATPAK_VERSION = 1.18.4
FLATPAK_SOURCE = flatpak-$(FLATPAK_VERSION).tar.xz
FLATPAK_SITE = https://github.com/flatpak/flatpak/releases/download/$(FLATPAK_VERSION)
FLATPAK_LICENSE = LGPL-2.1+
FLATPAK_LICENSE_FILES = COPYING
FLATPAK_INSTALL_STAGING = YES
# host-python-pyparsing: the variant-schema compiler runs at build time.
FLATPAK_DEPENDENCIES = host-pkgconf host-bison host-gettext host-libglib2 \
	host-python3 host-python-pyparsing \
	appstream bubblewrap xdg-dbus-proxy gdk-pixbuf json-glib libarchive \
	libcap libcurl libfuse3 libglib2 libgpgme libostree libxml2 polkit systemd
# The image's own bwrap, xdg-dbus-proxy and fusermount3 rather than the copies
# bundled as subprojects. wheel is PengyOS's admin group (polkit rules,
# 50-pengyos-admin.rules).
FLATPAK_CONF_OPTS = \
	-Dsystem_bubblewrap=/usr/bin/bwrap \
	-Dsystem_dbus_proxy=/usr/bin/xdg-dbus-proxy \
	-Dsystem_fusermount=/usr/bin/fusermount3 \
	-Dsystem_helper=enabled \
	-Dprivileged_group=wheel \
	-Dsystemd=enabled \
	-Dseccomp=enabled \
	-Ddconf=disabled \
	-Dmalcontent=disabled \
	-Dselinux_module=disabled \
	-Dgir=disabled \
	-Dgtkdoc=disabled \
	-Dman=disabled \
	-Ddocbook_docs=disabled \
	-Dtests=false \
	-Dinstalled_tests=false

FLATPAK_DEPENDENCIES += libseccomp

# The system helper's unprivileged user. Upstream ships a sysusers.d entry, but
# the image has no systemd-sysusers: Buildroot creates users at build time.
define FLATPAK_USERS
	flatpak -1 flatpak -1 * - - - Flatpak system helper
endef

ifeq ($(BR2_PACKAGE_ZSTD),y)
FLATPAK_CONF_OPTS += -Dlibzstd=enabled
FLATPAK_DEPENDENCIES += zstd
else
FLATPAK_CONF_OPTS += -Dlibzstd=disabled
endif

# X11 apps under Xwayland get an Xauthority file.
ifeq ($(BR2_PACKAGE_XLIB_LIBXAU),y)
FLATPAK_CONF_OPTS += -Dxauth=enabled
FLATPAK_DEPENDENCIES += xlib_libXau
else
FLATPAK_CONF_OPTS += -Dxauth=disabled
endif

ifeq ($(BR2_PACKAGE_WAYLAND)$(BR2_PACKAGE_WAYLAND_PROTOCOLS),yy)
FLATPAK_CONF_OPTS += -Dwayland_security_context=enabled
FLATPAK_DEPENDENCIES += wayland wayland-protocols host-wayland
else
FLATPAK_CONF_OPTS += -Dwayland_security_context=disabled
endif

$(eval $(meson-package))
