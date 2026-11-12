################################################################################
#
# libnotify
#
################################################################################

LIBNOTIFY_VERSION = 0.8.9
LIBNOTIFY_SOURCE = libnotify-$(LIBNOTIFY_VERSION).tar.xz
LIBNOTIFY_SITE = https://download.gnome.org/sources/libnotify/0.8
LIBNOTIFY_LICENSE = LGPL-2.1+
LIBNOTIFY_LICENSE_FILES = COPYING
LIBNOTIFY_INSTALL_STAGING = YES
LIBNOTIFY_DEPENDENCIES = libglib2 gdk-pixbuf
LIBNOTIFY_CONF_OPTS = \
	-Dtests=false \
	-Dintrospection=disabled \
	-Dman=false \
	-Dgtk_doc=false \
	-Ddocbook_docs=disabled

# CPE (NVD vendor:product) so pkg-stats can match CVEs; verified against NVD 2026-11-12.
LIBNOTIFY_CPE_ID_VENDOR = libnotify_project

$(eval $(meson-package))
