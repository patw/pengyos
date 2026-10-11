################################################################################
#
# xdg-desktop-portal
#
# Upstream pulls two git meson subprojects (libglnx, gvdb) that Buildroot does
# not package, and meson's wrap downloads are disabled in Buildroot. Fetch the
# pinned revisions as tarballs alongside the main source (hash-checked, and
# cached in DL_DIR like any download; it used to `git clone` during the build)
# and unpack them into subprojects/.
#
################################################################################

XDG_DESKTOP_PORTAL_VERSION = 1.22.1
XDG_DESKTOP_PORTAL_SOURCE = $(XDG_DESKTOP_PORTAL_VERSION).tar.gz
XDG_DESKTOP_PORTAL_SITE = https://github.com/flatpak/xdg-desktop-portal/archive/refs/tags
XDG_DESKTOP_PORTAL_LICENSE = LGPL-2.1-only
XDG_DESKTOP_PORTAL_LICENSE_FILES = COPYING

XDG_DESKTOP_PORTAL_DEPENDENCIES = host-pkgconf libglib2 json-glib libfuse3 gdk-pixbuf gstreamer1 gst1-plugins-base pipewire libgudev
XDG_DESKTOP_PORTAL_CONF_OPTS = -Dtests=disabled -Dinstalled-tests=false

# The Location portal is only compiled in with libgeoclue ('auto' silently left
# it out: Flatpak apps and COSMIC Settings' sunset-based dark mode found no
# org.freedesktop.portal.Location).
ifeq ($(BR2_PACKAGE_GEOCLUE),y)
XDG_DESKTOP_PORTAL_DEPENDENCIES += geoclue
XDG_DESKTOP_PORTAL_CONF_OPTS += -Dgeoclue=enabled
else
XDG_DESKTOP_PORTAL_CONF_OPTS += -Dgeoclue=disabled
endif
# Staging too: portal backends (xdg-desktop-portal-gtk) build against its .pc
# and the D-Bus interface XML it installs.
XDG_DESKTOP_PORTAL_INSTALL_STAGING = YES

LIBGLNX_REV = ff64d52116ae74f0d25e24f089db28921ea171ff
GVDB_REV = c6f2359cc1d00f16e0a0e2527fa0bc1882b8b5ab
XDG_DESKTOP_PORTAL_EXTRA_DOWNLOADS = \
	https://gitlab.gnome.org/GNOME/libglnx/-/archive/$(LIBGLNX_REV)/libglnx-$(LIBGLNX_REV).tar.gz \
	https://gitlab.gnome.org/GNOME/gvdb/-/archive/$(GVDB_REV)/gvdb-$(GVDB_REV).tar.gz

define XDG_DESKTOP_PORTAL_VENDOR_SUBPROJECTS
	rm -rf $(@D)/subprojects/libglnx $(@D)/subprojects/gvdb
	mkdir -p $(@D)/subprojects/libglnx $(@D)/subprojects/gvdb
	$(TAR) xzf $(XDG_DESKTOP_PORTAL_DL_DIR)/libglnx-$(LIBGLNX_REV).tar.gz \
		--strip-components=1 -C $(@D)/subprojects/libglnx
	$(TAR) xzf $(XDG_DESKTOP_PORTAL_DL_DIR)/gvdb-$(GVDB_REV).tar.gz \
		--strip-components=1 -C $(@D)/subprojects/gvdb
endef
XDG_DESKTOP_PORTAL_POST_EXTRACT_HOOKS += XDG_DESKTOP_PORTAL_VENDOR_SUBPROJECTS

$(eval $(meson-package))
