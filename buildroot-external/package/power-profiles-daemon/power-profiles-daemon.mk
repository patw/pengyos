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

# upower-pengyos, not upower: the fork is what ships (divergence.toml ->
# upower-1.91.5-fork). ppd talks to UPower over D-Bus only (it links no
# libupower-glib); the dependency orders it after the daemon's install.
POWER_PROFILES_DAEMON_DEPENDENCIES = host-pkgconf libglib2 libgudev upower-pengyos polkit systemd
POWER_PROFILES_DAEMON_CONF_OPTS = \
	-Dtests=false \
	-Dgtk_doc=false \
	-Dmanpage=disabled \
	-Dbashcomp=disabled \
	-Dpylint=disabled \
	-Dsystemdsystemunitdir=/usr/lib/systemd/system

$(eval $(meson-package))
