################################################################################
#
# xdg-desktop-portal-gtk
#
################################################################################

XDG_DESKTOP_PORTAL_GTK_VERSION = 1.15.3
XDG_DESKTOP_PORTAL_GTK_SOURCE = xdg-desktop-portal-gtk-$(XDG_DESKTOP_PORTAL_GTK_VERSION).tar.xz
XDG_DESKTOP_PORTAL_GTK_SITE = https://github.com/flatpak/xdg-desktop-portal-gtk/releases/download/$(XDG_DESKTOP_PORTAL_GTK_VERSION)
XDG_DESKTOP_PORTAL_GTK_LICENSE = LGPL-2.1+
XDG_DESKTOP_PORTAL_GTK_LICENSE_FILES = COPYING
XDG_DESKTOP_PORTAL_GTK_DEPENDENCIES = host-pkgconf libglib2 libgtk3 xdg-desktop-portal

# Wallpaper needs gnome-desktop. Settings is enabled, as on Pop!_OS: COSMIC's
# backend serves only org.freedesktop.appearance, and this one serves the
# org.gnome.* gsettings that cosmic-settings-daemon writes -- above all
# org.gnome.desktop.wm.preferences button-layout. A Flatpak GTK app (Firefox,
# Chrome, VS Code) reads its titlebar buttons from that key through the
# portal; without it GTK falls back to "menu:close" and they had a close
# button only.
XDG_DESKTOP_PORTAL_GTK_DEPENDENCIES += fontconfig gsettings-desktop-schemas
XDG_DESKTOP_PORTAL_GTK_CONF_OPTS = \
	-Dappchooser=enabled \
	-Dlockdown=enabled \
	-Dsettings=enabled \
	-Dwallpaper=disabled

# The portal interface XML is found through xdg-desktop-portal.pc's
# interfaces_dir, ${prefix}/share/dbus-1/interfaces: a target path. pkgconf's
# sysroot applies to -I/-L, not to variables; resolve it in staging.
define XDG_DESKTOP_PORTAL_GTK_STAGING_INTERFACES
	$(SED) "s#get_variable(pkgconfig: 'interfaces_dir')#get_variable(pkgconfig: 'interfaces_dir', pkgconfig_define: ['prefix', '$(STAGING_DIR)/usr'])#" \
		$(@D)/meson.build
endef
XDG_DESKTOP_PORTAL_GTK_POST_PATCH_HOOKS += XDG_DESKTOP_PORTAL_GTK_STAGING_INTERFACES

$(eval $(meson-package))
