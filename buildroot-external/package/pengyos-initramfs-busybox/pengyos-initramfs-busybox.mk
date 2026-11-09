################################################################################
#
# pengyos-initramfs-busybox: a static busybox for the installer ISO's initramfs
#
# The busybox release Buildroot's own package pins (its tarball, from the same
# dl/busybox directory), its own
# minimal config (allnoconfig + busybox.config), built static and installed to
# BINARIES_DIR only. board/common/iso-image.sh packs it with board/common/iso/init.
#
################################################################################

# Kept equal to BUSYBOX_VERSION (Buildroot's): the .hash is copied from it.
PENGYOS_INITRAMFS_BUSYBOX_VERSION = 1.38.0
PENGYOS_INITRAMFS_BUSYBOX_SOURCE = busybox-$(PENGYOS_INITRAMFS_BUSYBOX_VERSION).tar.bz2
PENGYOS_INITRAMFS_BUSYBOX_SITE = https://www.busybox.net/downloads
PENGYOS_INITRAMFS_BUSYBOX_DL_SUBDIR = busybox
PENGYOS_INITRAMFS_BUSYBOX_LICENSE = GPL-2.0, bzip2-1.0.4
PENGYOS_INITRAMFS_BUSYBOX_LICENSE_FILES = LICENSE archival/libarchive/bz/LICENSE
PENGYOS_INITRAMFS_BUSYBOX_INSTALL_TARGET = NO
PENGYOS_INITRAMFS_BUSYBOX_INSTALL_IMAGES = YES

PENGYOS_INITRAMFS_BUSYBOX_MAKE_OPTS = \
	CC="$(TARGET_CC)" \
	CROSS_COMPILE="$(TARGET_CROSS)" \
	ARCH=$(NORMALIZED_ARCH) \
	EXTRA_LDFLAGS="-static"

# busybox's kconfig keeps the FIRST setting of a symbol, so allnoconfig's
# "# CONFIG_X is not set" line for each symbol busybox.config sets is deleted
# (a sed script made from busybox.config) before busybox.config is appended.
define PENGYOS_INITRAMFS_BUSYBOX_CONFIGURE_CMDS
	$(MAKE) -C $(@D) $(PENGYOS_INITRAMFS_BUSYBOX_MAKE_OPTS) allnoconfig
	sed -n 's/^\(CONFIG_[A-Z0-9_]*\)=.*/\/^# \1 is not set$$\/d/p' \
		$(PENGYOS_INITRAMFS_BUSYBOX_PKGDIR)/busybox.config > $(@D)/.pengyos-drop.sed
	sed -i -f $(@D)/.pengyos-drop.sed $(@D)/.config
	cat $(PENGYOS_INITRAMFS_BUSYBOX_PKGDIR)/busybox.config >> $(@D)/.config
	yes "" | $(MAKE) -C $(@D) $(PENGYOS_INITRAMFS_BUSYBOX_MAKE_OPTS) oldconfig >/dev/null
endef

define PENGYOS_INITRAMFS_BUSYBOX_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) $(PENGYOS_INITRAMFS_BUSYBOX_MAKE_OPTS) busybox
endef

define PENGYOS_INITRAMFS_BUSYBOX_INSTALL_IMAGES_CMDS
	$(INSTALL) -D -m 0755 $(@D)/busybox $(BINARIES_DIR)/initramfs-busybox
endef

$(eval $(generic-package))
