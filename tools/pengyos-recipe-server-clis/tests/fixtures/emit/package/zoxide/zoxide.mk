################################################################################
#
# zoxide
#
################################################################################

ZOXIDE_VERSION = 0.10.0
ZOXIDE_SITE = $(call github,ajeetdsouza,zoxide,v0.10.0)
ZOXIDE_LICENSE = MIT
ZOXIDE_LICENSE_FILES = LICENSE

$(eval $(cargo-package))
