################################################################################
#
# ddcutil
#
################################################################################

DDCUTIL_VERSION = 3.0.2
DDCUTIL_SOURCE = v$(DDCUTIL_VERSION).tar.gz
DDCUTIL_SITE = https://github.com/rockowitz/ddcutil/archive/refs/tags
DDCUTIL_LICENSE = GPL-2.0+
DDCUTIL_LICENSE_FILES = COPYING
DDCUTIL_INSTALL_STAGING = YES
# A git archive, not a release tarball: generate configure.
DDCUTIL_AUTORECONF = YES
DDCUTIL_DEPENDENCIES = host-pkgconf libglib2 jansson acl libdrm udev
DDCUTIL_CONF_OPTS = \
	--disable-x11 \
	--disable-usb \
	--disable-doxygen \
	--disable-testcases \
	--disable-build-timestamp \
	--enable-udev \
	--enable-drm

$(eval $(autotools-package))
