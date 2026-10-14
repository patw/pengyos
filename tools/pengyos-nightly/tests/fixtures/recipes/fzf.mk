################################################################################
#
# fzf
#
################################################################################

FZF_VERSION = 0.74.4
FZF_SITE = $(call github,junegunn,fzf,v$(FZF_VERSION))
FZF_GOMOD = github.com/junegunn/fzf
FZF_LICENSE = MIT
FZF_LICENSE_FILES = LICENSE
# No cgo: the fzf CLI links nothing outside the Go stdlib, and this keeps the
# cross build free of a target C toolchain dependency.
FZF_GO_ENV = CGO_ENABLED=no

define FZF_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/bin/fzf $(TARGET_DIR)/usr/bin/fzf
endef

$(eval $(golang-package))
