################################################################################
#
# btop
#
################################################################################

BTOP_VERSION = 1.4.7
BTOP_SITE = $(call github,aristocratos,btop,v1.4.7)
BTOP_LICENSE = Apache-2.0
BTOP_LICENSE_FILES = LICENSE

$(eval $(cmake-package))
