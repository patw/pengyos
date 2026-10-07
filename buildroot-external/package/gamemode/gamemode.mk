################################################################################
#
# gamemode
#
################################################################################

GAMEMODE_VERSION = 1.8.2
GAMEMODE_SOURCE = $(GAMEMODE_VERSION).tar.gz
GAMEMODE_SITE = https://github.com/FeralInteractive/gamemode/archive/refs/tags
GAMEMODE_LICENSE = BSD-3-Clause
GAMEMODE_LICENSE_FILES = LICENSE.txt
GAMEMODE_INSTALL_STAGING = YES
GAMEMODE_DEPENDENCIES = host-pkgconf dbus systemd inih polkit

# The COSMIC session bus comes from dbus-run-session, which activates through
# the service file's Exec= (gamemoded -d); the user unit is installed anyway.
GAMEMODE_CONF_OPTS = \
	-Dwith-examples=false \
	-Dwith-util=true \
	-Dwith-sd-bus-provider=systemd \
	-Dwith-systemd-user-unit-dir=/usr/lib/systemd/user \
	-Dwith-systemd-group-dir=/usr/lib/sysusers.d \
	-Dwith-dbus-service-dir=/usr/share/dbus-1/services \
	-Dwith-pam-limits-dir=/etc/security/limits.d

$(eval $(meson-package))
