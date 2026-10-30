################################################################################
#
# appstream
#
################################################################################

APPSTREAM_VERSION = 1.2.2
APPSTREAM_SOURCE = AppStream-$(APPSTREAM_VERSION).tar.xz
APPSTREAM_SITE = https://www.freedesktop.org/software/appstream/releases
# The release tarball's paths start with "./AppStream-<version>/".
APPSTREAM_STRIP_COMPONENTS = 2
APPSTREAM_LICENSE = LGPL-2.1+ (library), GPL-2.0+ (tools)
APPSTREAM_LICENSE_FILES = COPYING
APPSTREAM_INSTALL_STAGING = YES
APPSTREAM_DEPENDENCIES = host-pkgconf host-gperf host-gettext \
	libglib2 libcurl libxml2 libxmlb libfyaml
# Only the library and appstreamcli: no compose (needs gdk-pixbuf, cairo,
# librsvg, freetype), no Qt bindings, no docs or stemming.
APPSTREAM_CONF_OPTS = \
	-Dcompose=false \
	-Dqt=false \
	-Dvapi=false \
	-Dgir=false \
	-Dstemming=false \
	-Ddocs=false \
	-Dapidocs=false \
	-Dinstall-docs=false \
	-Dman=false \
	-Dbash-completion=false \
	-Dtools=true \
	-Ddisplay-detection=none

ifeq ($(BR2_PACKAGE_SYSTEMD),y)
APPSTREAM_CONF_OPTS += -Dsystemd=true
APPSTREAM_DEPENDENCIES += systemd
else
APPSTREAM_CONF_OPTS += -Dsystemd=false
endif

ifeq ($(BR2_PACKAGE_ZSTD),y)
APPSTREAM_CONF_OPTS += -Dzstd-support=true
APPSTREAM_DEPENDENCIES += zstd
else
APPSTREAM_CONF_OPTS += -Dzstd-support=false
endif

$(eval $(meson-package))
