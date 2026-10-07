################################################################################
#
# greetd
#
################################################################################

GREETD_VERSION = 0.10.3
GREETD_SOURCE = $(GREETD_VERSION).tar.gz
GREETD_SITE = https://github.com/kennylevinsen/greetd/archive/refs/tags
GREETD_LICENSE = GPL-3.0-only
GREETD_LICENSE_FILES = LICENSE

$(eval $(cargo-package))
