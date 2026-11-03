################################################################################
#
# libdisplay-info-pengyos - PengyOS's fork of the stock libdisplay-info package.
#
# Why this file exists: divergence.toml -> libdisplay-info-0.3.0-fork. cosmic-comp
# links libdisplay-info.so.3 through the libdisplay-info-sys 0.3.0 crate, whose
# system-deps range is ">= 0.1.0, < 0.4.0". Buildroot 2026.08 ships 0.4.0, which
# is soversion 4 (meson.build: soversion = the minor version), so the stock
# package cannot serve the pinned compositor. Until this recipe, Desktop
# post-build copied the build host's Ubuntu libdisplay-info3 into the image,
# where no observe, CVE match or SBOM could see it.
#
# A faithful copy of package/libdisplay-info/libdisplay-info.mk from Buildroot
# 2026.08 with the version moved back to 0.3.0 and every symbol prefixed
# (LIBDISPLAY_INFO_ -> LIBDISPLAY_INFO_PENGYOS_). The tarball's name and site are
# upstream's, so the stock SOURCE line is kept rather than derived from the
# package name.
#
################################################################################

LIBDISPLAY_INFO_PENGYOS_VERSION = 0.3.0
LIBDISPLAY_INFO_PENGYOS_SOURCE = libdisplay-info-$(LIBDISPLAY_INFO_PENGYOS_VERSION).tar.xz
LIBDISPLAY_INFO_PENGYOS_SITE = https://gitlab.freedesktop.org/emersion/libdisplay-info/-/releases/$(LIBDISPLAY_INFO_PENGYOS_VERSION)/downloads
LIBDISPLAY_INFO_PENGYOS_LICENSE = MIT
LIBDISPLAY_INFO_PENGYOS_LICENSE_FILES = LICENSE
# No CPE, as in the stock recipe: NVD lists no CPE product for libdisplay-info
# (CPE API keywordSearch=libdisplay-info: 0 results, 2026-10-23), so pkg-stats
# cannot match its CVEs; the ledger entry carries them by hand.
LIBDISPLAY_INFO_PENGYOS_INSTALL_STAGING = YES

# Our own fix: upstream has none yet (rule 1 of patches/README.md allows it only in
# a recipe we author, which this is). CVE-2026-112272 is displayid2.c data block
# parsing, which 0.3.0 does not have.
# 0001-displayid-check-padding-only-over-the-bytes-the-section-has-left.patch
LIBDISPLAY_INFO_PENGYOS_IGNORE_CVES += CVE-2026-112271
LIBDISPLAY_INFO_PENGYOS_DEPENDENCIES = hwdata

# workaround for static_assert on uclibc-ng < 1.0.42
LIBDISPLAY_INFO_PENGYOS_CFLAGS += $(TARGET_CFLAGS) -Dstatic_assert=_Static_assert

# Images before this recipe carried the build host's copy as a regular file at
# /usr/lib/libdisplay-info.so.3 (Desktop post-build cp -L), and Buildroot never
# deletes from target/. meson installs .so.3 as a symlink and refuses ("already
# exists and is not a symlink"), so every existing Desktop tree failed here on
# 2026-10-23. Remove only a non-symlink: a symlink is this package's own.
define LIBDISPLAY_INFO_PENGYOS_REMOVE_HOST_COPY
	if [ -f $(TARGET_DIR)/usr/lib/libdisplay-info.so.3 ] && \
	   [ ! -L $(TARGET_DIR)/usr/lib/libdisplay-info.so.3 ]; then \
		rm -f $(TARGET_DIR)/usr/lib/libdisplay-info.so.3; \
	fi
endef
LIBDISPLAY_INFO_PENGYOS_PRE_INSTALL_TARGET_HOOKS += LIBDISPLAY_INFO_PENGYOS_REMOVE_HOST_COPY

$(eval $(meson-package))
