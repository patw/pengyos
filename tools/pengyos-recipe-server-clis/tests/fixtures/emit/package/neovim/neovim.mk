################################################################################
#
# neovim
#
################################################################################

NEOVIM_VERSION = 0.12.5
NEOVIM_SITE = $(call github,neovim,neovim,v0.12.5)
NEOVIM_LICENSE = Apache-2.0
NEOVIM_LICENSE_FILES = LICENSE.txt

$(eval $(cmake-package))
