################################################################################
#
# starship
#
################################################################################

STARSHIP_VERSION = 1.26.0
STARSHIP_SITE = $(call github, starship, starship, v$(STARSHIP_VERSION))
STARSHIP_LICENSE = ISC
STARSHIP_LICENSE_FILES = LICENSE

$(eval $(cargo-package))
