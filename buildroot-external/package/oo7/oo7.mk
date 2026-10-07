################################################################################
#
# oo7
#
################################################################################

# 0.7.0-beta, not 0.6.0: 0.6.0 ignores the --login password when there is no
# keyring yet, creating a locked, passwordless Login keyring that only a
# password prompt can set up -- and COSMIC has no prompter (oo7 has GNOME
# Shell's and KDE's). 0.7 creates Login with that password. Move to 0.7.0.
OO7_VERSION = 0.7.0.beta
OO7_SOURCE = $(OO7_VERSION).tar.gz
OO7_SITE = https://github.com/linux-credentials/oo7/archive/refs/tags
OO7_LICENSE = MIT
OO7_LICENSE_FILES = LICENSE
# The workspace has eight crates; build the daemon, then the portal (below).
OO7_SUBDIR = server

define OO7_INSTALL_PORTAL
	cd $(@D)/portal && \
	$(TARGET_MAKE_ENV) \
		$(TARGET_CONFIGURE_OPTS) \
		$(PKG_CARGO_ENV) \
		cargo install \
			--offline \
			--root $(TARGET_DIR)/usr/ \
			--bins \
			--path ./ \
			--no-track \
			--force \
			--locked \
			-Z target-applies-to-host
endef
OO7_POST_INSTALL_TARGET_HOOKS += OO7_INSTALL_PORTAL

# Upstream's D-Bus activation files hand off to systemd user units
# (Exec=/bin/false, SystemdService=); the COSMIC session bus comes from
# dbus-run-session, which activates only through Exec=. Ours exec directly;
# the secrets one through oo7-session, which unlocks the keyring.
define OO7_INSTALL_DATA
	$(INSTALL) -D -m 0755 $(OO7_PKGDIR)/oo7-session \
		$(TARGET_DIR)/usr/libexec/oo7-session
	$(INSTALL) -D -m 0644 $(OO7_PKGDIR)/org.freedesktop.secrets.service \
		$(TARGET_DIR)/usr/share/dbus-1/services/org.freedesktop.secrets.service
	$(INSTALL) -D -m 0644 $(OO7_PKGDIR)/org.freedesktop.impl.portal.desktop.oo7.service \
		$(TARGET_DIR)/usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.oo7.service
	$(INSTALL) -D -m 0644 $(OO7_PKGDIR)/oo7-portal.portal \
		$(TARGET_DIR)/usr/share/xdg-desktop-portal/portals/oo7-portal.portal
endef
OO7_POST_INSTALL_TARGET_HOOKS += OO7_INSTALL_DATA

$(eval $(cargo-package))
