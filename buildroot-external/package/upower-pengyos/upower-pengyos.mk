################################################################################
#
# upower-pengyos - PengyOS's fork of the stock upower package.
#
# Why this file exists: divergence.toml -> upower-1.91.5-fork. Buildroot 2026.08
# pins upower 0.99.19, and the stock recipe has not been touched since 2022-06
# (master included), while upstream is at 1.91.5. Two bugs that 0.99.19 carries
# are fixed upstream at 1.91.5:
#
#  - After resume from suspend the battery stops updating until upowerd restarts
#    or AC changes (found on beholaptop, 2026-10-20: the panel read 75% / 7.1 h
#    for 83 minutes while the kernel read 48% / 1.7 h). In 0.99.19, a poll that
#    fires while polling is paused for sleep returns without re-arming its
#    GSource, and up_daemon_resume_poll() only clears the flag. Upstream
#    e88e90fafa05 (2022-07-12, issue #198, first in 1.90.0) re-arms it.
#  - capacity_level read as "Ful" with libgudev >= 238 (upstream 6bfe66a, carried
#    on 0.99.19 as a patch set until this fork; the patch set is deleted).
#
# The fix is the version, and a stock version cannot be moved from a br2-external
# tree, so the shape is a named fork with an expiry - the same decision as
# openssh-pengyos, ffmpeg-pengyos and hplip-pengyos.
#
# A faithful copy of package/upower/upower.mk from Buildroot 2026.08 with the
# version moved and every symbol prefixed (UPOWER_ -> UPOWER_PENGYOS_). Deviations
# from the stock recipe are commented in place.
#
# thermald links libupower-glib and its stock Config.in selects
# BR2_PACKAGE_UPOWER, and a Kconfig select outranks a defconfig line, so thermald
# is forked with this package (package/thermald-pengyos): without that, stock
# 0.99.19 is built and installed beside this fork. libupower-glib keeps
# soversion 3 at 1.91.5, so the ABI thermald links against does not move.
#
################################################################################

UPOWER_PENGYOS_VERSION = 1.91.5
# Buildroot's default SOURCE is <pkg>-<version>.tar.gz, i.e.
# upower-pengyos-1.91.5.tar.gz, which upstream does not publish. Naming the
# gitlab archive here is what makes the download resolve, and the .hash line is
# keyed to this same name. (Same as openssh-pengyos and hplip-pengyos.)
UPOWER_PENGYOS_SOURCE = upower-v$(UPOWER_PENGYOS_VERSION).tar.bz2
UPOWER_PENGYOS_SITE = https://gitlab.freedesktop.org/upower/upower/-/archive/v$(UPOWER_PENGYOS_VERSION)
UPOWER_PENGYOS_LICENSE = GPL-2.0+
UPOWER_PENGYOS_LICENSE_FILES = COPYING
# No CPE, as in the stock recipe: NVD lists no CPE product for upower at all
# (CPE API keywordSearch=upower: 0 results, 2026-10-20), so there is nothing to
# name and no CVE feed for pkg-stats to match. The fork's staleness is watched
# by pengyos-nightly's upower-fork observe source instead.

# libupower-glib.so
UPOWER_PENGYOS_INSTALL_STAGING = YES

UPOWER_PENGYOS_DEPENDENCIES = \
	$(TARGET_NLS_DEPENDENCIES) \
	host-pkgconf \
	libgudev \
	udev

UPOWER_PENGYOS_CONF_OPTS = \
	-Dgtk-doc=false \
	-Dman=false \
	-Dsystemdsystemunitdir=/usr/lib/systemd/system \
	-Dudevhwdbdir=/lib/udev/hwdb.d \
	-Dudevrulesdir=/lib/udev/rules.d

# Options 1.91.5 adds over 0.99.19. Each is set rather than left at upstream's
# default, because "auto" makes the result depend on what else happened to be
# built first, and installed_tests and the zsh completion default to on.
UPOWER_PENGYOS_CONF_OPTS += \
	-Dinstalled_tests=false \
	-Dzshcompletiondir=no \
	-Didevice=disabled

# polkit guards the charge-threshold methods 1.91 adds (EnableChargeThreshold).
ifeq ($(BR2_PACKAGE_POLKIT),y)
UPOWER_PENGYOS_CONF_OPTS += -Dpolkit=enabled
UPOWER_PENGYOS_DEPENDENCIES += polkit
else
UPOWER_PENGYOS_CONF_OPTS += -Dpolkit=disabled
endif

ifeq ($(BR2_PACKAGE_GOBJECT_INTROSPECTION),y)
UPOWER_PENGYOS_CONF_OPTS += -Dintrospection=enabled
UPOWER_PENGYOS_DEPENDENCIES += gobject-introspection
else
UPOWER_PENGYOS_CONF_OPTS += -Dintrospection=disabled
endif

$(eval $(meson-package))
