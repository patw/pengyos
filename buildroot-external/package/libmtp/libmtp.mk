################################################################################
#
# libmtp
#
################################################################################

LIBMTP_VERSION = 1.1.23
LIBMTP_SITE = https://github.com/libmtp/libmtp/releases/download/v$(LIBMTP_VERSION)
LIBMTP_LICENSE = LGPL-2.1+
LIBMTP_LICENSE_FILES = COPYING
LIBMTP_INSTALL_STAGING = YES
LIBMTP_DEPENDENCIES = host-pkgconf libusb host-libmtp
# The build generates its udev rules/hwdb by running mtp-hotplug; cross-compiling,
# that has to be a host build of it (configure looks for HOST_MTP_HOTPLUG).
LIBMTP_CONF_ENV = ac_cv_prog_HOST_MTP_HOTPLUG=$(HOST_DIR)/bin/mtp-hotplug
LIBMTP_CONF_OPTS = --disable-doxygen --disable-mtpz --with-udev=/usr/lib/udev \
	--enable-crossbuilddir=$(TARGET_DIR)/usr/lib/udev

HOST_LIBMTP_DEPENDENCIES = host-pkgconf host-libusb
HOST_LIBMTP_CONF_OPTS = --disable-doxygen --disable-mtpz

# CPE (NVD vendor:product) so pkg-stats can match CVEs; verified against NVD 2026-11-12.
LIBMTP_CPE_ID_VENDOR = libmtp_project

$(eval $(autotools-package))
$(eval $(host-autotools-package))
