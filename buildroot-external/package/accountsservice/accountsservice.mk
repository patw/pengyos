################################################################################
#
# accountsservice
#
################################################################################

ACCOUNTSSERVICE_VERSION = 26.27.3
ACCOUNTSSERVICE_SITE = https://gitlab.freedesktop.org/accountsservice/accountsservice/-/archive/$(ACCOUNTSSERVICE_VERSION)
ACCOUNTSSERVICE_LICENSE = GPL-3.0+
ACCOUNTSSERVICE_LICENSE_FILES = COPYING
ACCOUNTSSERVICE_DEPENDENCIES = host-pkgconf libglib2 polkit systemd
# Administrators are the wheel group (sudo, the polkit admin rule). The wtmp
# path cannot be probed when cross-compiling; glibc's is /var/log/wtmp.
# Automatic login (its AutomaticLogin property, SetAutomaticLogin): the file
# greetd-config reads, in the GDM format AccountsService writes.
ACCOUNTSSERVICE_CONF_OPTS = \
	-Dadmin_group=wheel \
	-Dwtmpfile=/var/log/wtmp \
	-Dgdmconffile=/etc/pengyos/autologin.conf \
	-Dsystemdsystemunitdir=/usr/lib/systemd/system \
	-Dintrospection=false \
	-Dvapi=false \
	-Dlibxcrypt=false \
	-Ddocbook=false \
	-Dgtk_doc=false \
	-Dtests=false

$(eval $(meson-package))
