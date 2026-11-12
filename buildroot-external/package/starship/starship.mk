################################################################################
#
# starship
#
################################################################################

STARSHIP_VERSION = 1.27.0
STARSHIP_SITE = $(call github,starship,starship,v$(STARSHIP_VERSION))
STARSHIP_LICENSE = ISC
STARSHIP_LICENSE_FILES = LICENSE

# CPE (NVD vendor:product) so pkg-stats can match CVEs; verified against NVD 2026-11-12.
STARSHIP_CPE_ID_VENDOR = starship

$(eval $(cargo-package))
