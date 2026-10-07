################################################################################
#
# power-profiles-daemon
#
################################################################################

POWER_PROFILES_DAEMON_VERSION = 0.30
POWER_PROFILES_DAEMON_SOURCE = power-profiles-daemon-$(POWER_PROFILES_DAEMON_VERSION).tar.bz2
POWER_PROFILES_DAEMON_SITE = https://gitlab.freedesktop.org/upower/power-profiles-daemon/-/archive/$(POWER_PROFILES_DAEMON_VERSION)
POWER_PROFILES_DAEMON_LICENSE = GPL-3.0+
POWER_PROFILES_DAEMON_LICENSE_FILES = COPYING

POWER_PROFILES_DAEMON_DEPENDENCIES = host-pkgconf libglib2 libgudev upower polkit systemd
POWER_PROFILES_DAEMON_CONF_OPTS = \
	-Dtests=false \
	-Dgtk_doc=false \
	-Dmanpage=disabled \
	-Dbashcomp=disabled \
	-Dpylint=disabled \
	-Dsystemdsystemunitdir=/usr/lib/systemd/system

$(eval $(meson-package))
