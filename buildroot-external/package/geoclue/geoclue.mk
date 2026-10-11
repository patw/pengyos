################################################################################
#
# geoclue
#
# D-Bus-activated (geoclue.service), so nothing is enabled. Runs as the geoclue
# user. libgeoclue is what xdg-desktop-portal's Location portal links.
#
# Sources: Wi-Fi (BeaconDB, through wpa_supplicant's D-Bus API), IP fallback,
# and, when ModemManager is built, 3G/CDMA/modem GPS. Off: the NMEA source
# (a phone sharing GPS over mDNS) and the compass (iio-sensor-proxy, which the
# image does not ship).
#
# The demo agent is built: geoclue makes every client wait 5 s for an
# authorization agent when its whitelist names one, and the default whitelist
# names geoclue-demo-agent once it is built. It is started from
# /etc/xdg/autostart (cosmic-session runs XDG autostart). Apps reach geoclue
# through the portal, which asks the user itself; geoclue treats the
# unsandboxed portal as a system client, so the agent never prompts for it.
#
################################################################################

GEOCLUE_VERSION = 2.8.2
GEOCLUE_SITE = https://gitlab.freedesktop.org/geoclue/geoclue/-/archive/$(GEOCLUE_VERSION)
GEOCLUE_SOURCE = geoclue-$(GEOCLUE_VERSION).tar.bz2
GEOCLUE_LICENSE = GPL-2.0+ (service), LGPL-2.1+ (libgeoclue)
GEOCLUE_LICENSE_FILES = COPYING COPYING.LIB
GEOCLUE_INSTALL_STAGING = YES
GEOCLUE_DEPENDENCIES = host-pkgconf host-gettext host-libglib2 libglib2 json-glib libsoup3 libnotify
GEOCLUE_CONF_OPTS = \
	-Dlibgeoclue=true \
	-Dintrospection=false \
	-Dvapi=false \
	-Dgtk-doc=false \
	-Dwifi-source=true \
	-Dip-source=true \
	-Dstatic-source=true \
	-Dnmea-source=false \
	-Dcompass=false \
	-Ddemo-agent=true \
	-Denable-backend=true \
	-Ddbus-srv-user=geoclue \
	-Ddbus-sys-dir=/usr/share/dbus-1/system.d \
	-Dsystemd-system-unit-dir=/usr/lib/systemd/system

ifeq ($(BR2_PACKAGE_MODEM_MANAGER),y)
GEOCLUE_DEPENDENCIES += modem-manager
GEOCLUE_CONF_OPTS += -D3g-source=true -Dcdma-source=true -Dmodem-gps-source=true
else
GEOCLUE_CONF_OPTS += -D3g-source=false -Dcdma-source=false -Dmodem-gps-source=false
endif

# Upstream's geoclue.conf enables every source, including the two built out
# above, and geoclue warns about each at every start. It reads that file before
# /etc/geoclue/conf.d, so a drop-in can't silence it; switch them off in place.
define GEOCLUE_DISABLE_UNBUILT_SOURCES
	$(SED) '/^\[network-nmea\]/,/^\[/ s/^enable=true/enable=false/' \
		-e '/^\[compass\]/,/^\[/ s/^enable=true/enable=false/' \
		$(TARGET_DIR)/etc/geoclue/geoclue.conf
endef
GEOCLUE_POST_INSTALL_TARGET_HOOKS += GEOCLUE_DISABLE_UNBUILT_SOURCES

GEOCLUE_USERS = geoclue -1 geoclue -1 * /var/lib/geoclue - - GeoClue location service

$(eval $(meson-package))
