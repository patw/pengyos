################################################################################
#
# xdg-user-dirs
#
################################################################################

XDG_USER_DIRS_VERSION = 0.20
XDG_USER_DIRS_SOURCE = xdg-user-dirs-$(XDG_USER_DIRS_VERSION).tar.xz
XDG_USER_DIRS_SITE = https://user-dirs.freedesktop.org/releases
XDG_USER_DIRS_LICENSE = GPL-2.0+
XDG_USER_DIRS_LICENSE_FILES = COPYING
XDG_USER_DIRS_CONF_OPTS = -Ddocs=false -Dnls=false

$(eval $(meson-package))
