################################################################################
#
# hplip-pengyos - PengyOS's fork of the stock hplip package.
#
# Why this file exists: divergence.toml -> hplip-3.26.4-fork. Buildroot 2026.08
# pins hplip 3.17.10, released in 2018, and 9 P1 CVE matches sit inside it. NVD
# names no fix commit for any of them, so the patch shape is inadmissible
# (patches/README.md rule 1), and a stock version cannot be moved from a
# br2-external tree. The fix is the version, so the shape left is a named fork
# with an expiry - the same decision as openssh-10.6p1-fork and ffmpeg-9.0.2-fork.
#
# 3.26.6, not 3.26.4. Measured against NVD's own ranges: 3.26.4 closes 2 of the 9
# P1 matches and 3.26.5 closes 2, because seven of the nine name 3.26.6 as the
# first unaffected release. 3.26.6 closes all 9, including the two at CVSS 9.3, so
# it is the lowest release that clears the whole set - the same rule that picked
# 3.26.4, applied to all nine ranges instead of the one I first read.
#
# A faithful copy of package/hplip/hplip.mk and Config.in from Buildroot 2026.08
# with the version moved and every symbol prefixed (HPLIP_ -> HPLIP_PENGYOS_,
# BR2_PACKAGE_HPLIP -> BR2_PACKAGE_HPLIP_PENGYOS), carrying Buildroot's two
# build-system patches with it. Deviations from the stock recipe are commented in
# place.
#
# The CPE is named by the stock recipe already (vendor hp, product
# linux_imaging_and_printing), so the fork keeps it and pkg-stats can see this
# package's CVEs - the failure the openssh fork hit does not apply here.
#
################################################################################

HPLIP_PENGYOS_VERSION = 3.26.6
HPLIP_PENGYOS_SITE = https://prdownloads.sourceforge.net/hplip/hplip
# Buildroot's default SOURCE is <pkg>-<version>.tar.gz, i.e.
# hplip-pengyos-3.26.4.tar.gz, which upstream does not publish: the tarball is
# named hplip-3.26.4.tar.gz. Naming it here is what makes the download resolve -
# without it Buildroot tries its own mirror under the fork's name and gets a 404.
# The .hash line is keyed to this same name. (Same as openssh-pengyos.)
HPLIP_PENGYOS_SOURCE = hplip-$(HPLIP_PENGYOS_VERSION).tar.gz
HPLIP_PENGYOS_AUTORECONF = YES
HPLIP_PENGYOS_DEPENDENCIES = cups libusb jpeg host-pkgconf
HPLIP_PENGYOS_LICENSE = GPL-2.0, BSD-3-Clause, MIT
HPLIP_PENGYOS_LICENSE_FILES = COPYING
HPLIP_PENGYOS_CPE_ID_VENDOR = hp
HPLIP_PENGYOS_CPE_ID_PRODUCT = linux_imaging_and_printing

HPLIP_PENGYOS_CONF_OPTS = \
	--disable-qt4 \
	--disable-scan-build \
	--disable-gui-build \
	--disable-doc-build \
	--disable-network-build \
	--enable-hpcups-install \
	--disable-hpijs-install \
	--enable-cups-ppd-install \
	--enable-cups-drv-install \
	--disable-foomatic-ppd-install \
	--disable-foomatic-drv-install \
	--disable-foomatic-rip-hplip-install \
	--enable-new-hpcups \
	--enable-lite-build

# build system does not support cups-config, so the libs come from cups-config.
# -std=gnu17 -fpermissive, not the compiler default. Buildroot 2026.08's gcc is
# 15.3, which defaults to C23 and turns implicit int into a *permerror* - an
# error that no dialect setting and no -Wno-error= can undo. hplip 3.26.4 has
# K&R-era declarations, e.g. prnt/hpps/pserror.c:27 `static column = 0 ;`:
#   prnt/hpps/pserror.c:27:10: error: type defaults to 'int' in declaration of 'column' [-Wimplicit-int]
# Measured on this toolchain: `-std=gnu17` alone still errors, `-Wno-error=...`
# cannot override a permerror, and `-fpermissive` is what downgrades it back to
# a warning. -fpermissive is chosen over -Wno-implicit-int because it keeps the
# diagnostic in the build log: the code is old, and that should be visible
# rather than silenced. Pinning the older dialect is what keeps upstream's own
# source compiling; the alternative is editing hplip's C, which is not ours to
# write.
#
# It has to be carried in CONF_ENV as a CFLAGS= assignment, not in
# HPLIP_PENGYOS_CFLAGS: pkg-autotools.mk builds the configure environment as
# $(TARGET_CONFIGURE_OPTS) $(TARGET_CONFIGURE_ARGS) $($(PKG)_CONF_ENV), and
# TARGET_CONFIGURE_OPTS carries no CFLAGS - so a <PKG>_CFLAGS line is simply
# never read, and the flag silently does nothing. CONF_ENV comes last in that
# list, so its CFLAGS wins. This is also what makes 0003-...save-CFLAGS...
# matter: without it the restored CFLAGS is empty and the flags are dropped
# again on their way into the Makefile.
HPLIP_PENGYOS_CONF_ENV = LIBS=`$(STAGING_DIR)/usr/bin/cups-config --libs` \
	CFLAGS="$(TARGET_CFLAGS) -std=gnu17 -fpermissive"

ifeq ($(BR2_PACKAGE_DBUS),y)
HPLIP_PENGYOS_CONF_OPTS += --enable-dbus-build
HPLIP_PENGYOS_DEPENDENCIES += dbus
else
HPLIP_PENGYOS_CONF_OPTS += --disable-dbus-build
endif

define HPLIP_PENGYOS_POST_INSTALL_TARGET_FIXUP
	mkdir -p $(TARGET_DIR)/usr/share/hplip/data/models
	cp $(@D)/data/models/* $(TARGET_DIR)/usr/share/hplip/data/models
endef
HPLIP_PENGYOS_POST_INSTALL_TARGET_HOOKS += HPLIP_PENGYOS_POST_INSTALL_TARGET_FIXUP

$(eval $(autotools-package))
