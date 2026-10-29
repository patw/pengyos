################################################################################
#
# bolt
#
################################################################################

BOLT_VERSION = 0.9.11
BOLT_SOURCE = bolt-$(BOLT_VERSION).tar.bz2
BOLT_SITE = https://gitlab.freedesktop.org/bolt/bolt/-/archive/$(BOLT_VERSION)
BOLT_LICENSE = LGPL-2.1+
# Without a CPE vendor, pkg-stats matches on the bare name, and NVD's only "bolt"
# product is Bolt CMS (cpe:2.3:a:bolt:bolt): 13 of its CVEs were reported against this
# Thunderbolt daemon. NVD has no CPE for freedesktop's bolt, so name the vendor and
# match nothing rather than the wrong project.
BOLT_CPE_ID_VENDOR = freedesktop
BOLT_LICENSE_FILES = COPYING
BOLT_DEPENDENCIES = host-pkgconf libglib2 udev polkit systemd
BOLT_CONF_OPTS = -Dman=false -Dinstall-tests=false

$(eval $(meson-package))
