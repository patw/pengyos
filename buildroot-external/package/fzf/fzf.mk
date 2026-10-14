################################################################################
#
# fzf
#
################################################################################

FZF_VERSION = 0.74.5
FZF_SITE = $(call github,junegunn,fzf,v$(FZF_VERSION))
FZF_GOMOD = github.com/junegunn/fzf
FZF_LICENSE = MIT
FZF_LICENSE_FILES = LICENSE
# No cgo: the fzf CLI links nothing outside the Go stdlib, and this keeps the
# cross build free of a target C toolchain dependency.
FZF_GO_ENV = CGO_ENABLED=no

# The golang infra builds the binary into $(@D)/bin/ (not $(@D)/, where the
# first version looked for it and failed the build); the shell integration
# (the pieces the PengyOS comfort profile sources: key bindings + completion,
# for bash and zsh) and the fzf-tmux helper are plain files upstream ships in
# the tree.
define FZF_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/bin/fzf $(TARGET_DIR)/usr/bin/fzf
	$(INSTALL) -D -m 0755 $(@D)/bin/fzf-tmux $(TARGET_DIR)/usr/bin/fzf-tmux
	mkdir -p $(TARGET_DIR)/usr/share/fzf
	$(INSTALL) -m 0644 $(@D)/shell/key-bindings.bash $(TARGET_DIR)/usr/share/fzf/
	$(INSTALL) -m 0644 $(@D)/shell/key-bindings.zsh  $(TARGET_DIR)/usr/share/fzf/
	$(INSTALL) -m 0644 $(@D)/shell/completion.bash    $(TARGET_DIR)/usr/share/fzf/
	$(INSTALL) -m 0644 $(@D)/shell/completion.zsh     $(TARGET_DIR)/usr/share/fzf/
endef

$(eval $(golang-package))
