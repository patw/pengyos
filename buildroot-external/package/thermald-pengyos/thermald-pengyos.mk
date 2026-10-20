################################################################################
#
# thermald-pengyos - stock thermald, rebuilt against upower-pengyos.
#
# Why this file exists: divergence.toml -> upower-1.91.5-fork. This is not a
# version change. thermald 2.5.6 links libupower-glib, and the stock Config.in
# selects BR2_PACKAGE_UPOWER: a Kconfig select outranks a defconfig line, so
# with stock thermald in the config the vulnerable-to-our-bug upower 0.99.19 is
# built and installed beside the fork - two packages writing /usr/libexec/upowerd
# and pkg-stats reporting both. Same trap as mosh -> openssh (openssh-10.6p1-fork).
#
# A faithful copy of package/thermald/thermald.mk from Buildroot 2026.08, same
# version and hash, with every symbol prefixed (THERMALD_ -> THERMALD_PENGYOS_)
# and one deviation, commented in place: `upower` -> `upower-pengyos`. It is
# deleted together with upower-pengyos.
#
################################################################################

THERMALD_PENGYOS_VERSION = 2.5.6
THERMALD_PENGYOS_SITE = $(call github,intel,thermal_daemon,v$(THERMALD_PENGYOS_VERSION))
# The stock recipe's tarball is thermald-<version>.tar.gz (the github helper
# names it after the package); name it so the fork downloads the same file and
# the hash below is the stock recipe's own line.
THERMALD_PENGYOS_SOURCE = thermald-$(THERMALD_PENGYOS_VERSION).tar.gz
# fetched from Github, with no configure script
THERMALD_PENGYOS_AUTORECONF = YES
THERMALD_PENGYOS_AUTORECONF_OPTS = --include=$(HOST_DIR)/share/autoconf-archive
# Deviation: upower-pengyos, not upower (see the header).
THERMALD_PENGYOS_DEPENDENCIES = \
	$(TARGET_NLS_DEPENDENCIES) \
	host-autoconf-archive \
	dbus \
	dbus-glib \
	libevdev \
	libxml2 \
	upower-pengyos \
	xz
# tools are GPL-3.0+ but are not added to the target
THERMALD_PENGYOS_LICENSE = GPL-2.0+
THERMALD_PENGYOS_LICENSE_FILES = COPYING
THERMALD_PENGYOS_CPE_ID_VENDOR = intel
# The stock recipe leaves the product to default to the package name, which is
# right for "thermald"; the fork's name is not, so it is named.
THERMALD_PENGYOS_CPE_ID_PRODUCT = thermald
THERMALD_PENGYOS_CONF_ENV = LIBS=$(TARGET_NLS_LIBS)

# avoid this error when reconfiguring:
# automake: error: cannot open < gtk-doc.make: No such file or directory
define THERMALD_PENGYOS_GTK_DOC_HOOK
	echo "CLEANFILES=" > $(@D)/gtk-doc.make
endef
THERMALD_PENGYOS_PRE_CONFIGURE_HOOKS += THERMALD_PENGYOS_GTK_DOC_HOOK

# Autoreconf is missing the m4/ directory, which might actually be missing
# if it was the first argument, but unfortunately we are overriding the
# first include directory above. Thus we need that hook here.
define THERMALD_PENGYOS_CREATE_M4
	mkdir -p $(@D)/m4
endef
THERMALD_PENGYOS_POST_PATCH_HOOKS += THERMALD_PENGYOS_CREATE_M4

ifeq ($(BR2_INIT_SYSTEMD),y)
THERMALD_PENGYOS_DEPENDENCIES += systemd
THERMALD_PENGYOS_CONF_OPTS += --with-systemdsystemunitdir=/usr/lib/systemd/system
endif

$(eval $(autotools-package))
