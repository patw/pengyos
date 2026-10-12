################################################################################
#
# zsh-syntax-highlighting
#
################################################################################

ZSH_SYNTAX_HIGHLIGHTING_VERSION = 0.8.0
ZSH_SYNTAX_HIGHLIGHTING_SITE = $(call github,zsh-users,zsh-syntax-highlighting,$(ZSH_SYNTAX_HIGHLIGHTING_VERSION))
ZSH_SYNTAX_HIGHLIGHTING_LICENSE = MIT
ZSH_SYNTAX_HIGHLIGHTING_LICENSE_FILES = COPYING.md

# Pure shell scripts (no build): highlights the command line as you type —
# the second half of the oh-my-zsh feel. Sourced LAST by the comfort profile
# (/etc/pengyos/shell/profile): it wraps the ZLE widgets. Its git highlighter
# shells out to git, hence the runtime dependency.
define ZSH_SYNTAX_HIGHLIGHTING_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/share/zsh/plugins
	cp -r $(@D) $(TARGET_DIR)/usr/share/zsh/plugins/zsh-syntax-highlighting
endef

$(eval $(generic-package))
