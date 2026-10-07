################################################################################
#
# ldacbt
#
################################################################################

LDACBT_VERSION = 2.0.2.6
LDACBT_SOURCE = ldacBT-$(LDACBT_VERSION).tar.gz
LDACBT_SITE = https://github.com/EHfive/ldacBT/releases/download/v$(LDACBT_VERSION)
LDACBT_LICENSE = Apache-2.0
LDACBT_LICENSE_FILES = LICENSE
LDACBT_INSTALL_STAGING = YES
# The release tarball has no top-level directory.
LDACBT_STRIP_COMPONENTS = 0
LDACBT_CONF_OPTS = -DLDAC_SOFT_FLOAT=OFF

$(eval $(cmake-package))
