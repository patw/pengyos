################################################################################
#
# rtkit
#
# D-Bus-activated (rtkit-daemon.service, SystemdService= in the bus service
# file), so nothing is enabled. The daemon drops to the rtkit user and chroots
# into /proc; upstream leaves creating that user to the distribution.
#
################################################################################

RTKIT_VERSION = 0.14
# Upstream moved from github.com/heftig/rtkit (last tag v0.13, 2020) to the PipeWire
# group on freedesktop.org, where 0.14 was released (2025-12-10).
RTKIT_SITE = https://gitlab.freedesktop.org/pipewire/rtkit/-/archive/v$(RTKIT_VERSION)
RTKIT_SOURCE = rtkit-v$(RTKIT_VERSION).tar.gz
RTKIT_LICENSE = GPL-3.0+ (daemon), MIT (client library)
RTKIT_LICENSE_FILES = GPL LICENSE
RTKIT_DEPENDENCIES = host-pkgconf dbus libcap systemd polkit
RTKIT_CONF_OPTS = \
	-Dlibsystemd=enabled \
	-Dinstalled_tests=false \
	-Ddbus_interfacedir=/usr/share/dbus-1/interfaces \
	-Ddbus_rulesdir=/usr/share/dbus-1/system.d \
	-Ddbus_systemservicedir=/usr/share/dbus-1/system-services \
	-Dpolkit_actiondir=/usr/share/polkit-1/actions \
	-Dsystemd_systemunitdir=/usr/lib/systemd/system

RTKIT_USERS = rtkit -1 rtkit -1 * /proc - - RealtimeKit

$(eval $(meson-package))
