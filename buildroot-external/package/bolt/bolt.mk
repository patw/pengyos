################################################################################
#
# bolt
#
################################################################################

BOLT_VERSION = 0.9.11
BOLT_SOURCE = bolt-$(BOLT_VERSION).tar.bz2
BOLT_SITE = https://gitlab.freedesktop.org/bolt/bolt/-/archive/$(BOLT_VERSION)
BOLT_LICENSE = LGPL-2.1+
BOLT_LICENSE_FILES = COPYING
BOLT_DEPENDENCIES = host-pkgconf libglib2 udev polkit systemd
BOLT_CONF_OPTS = -Dman=false -Dinstall-tests=false

$(eval $(meson-package))
