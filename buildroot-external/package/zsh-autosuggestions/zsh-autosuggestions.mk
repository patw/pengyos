################################################################################
#
# zsh-autosuggestions
#
################################################################################

ZSH_AUTOSUGGESTIONS_VERSION = 0.7.1
ZSH_AUTOSUGGESTIONS_SITE = $(call github,zsh-users,zsh-autosuggestions,v$(ZSH_AUTOSUGGESTIONS_VERSION))
ZSH_AUTOSUGGESTIONS_LICENSE = MIT
ZSH_AUTOSUGGESTIONS_LICENSE_FILES = LICENSE

# Pure shell script (no build): suggests the rest of a history command as you
# type; accept with Right. The zsh half of the oh-my-zsh feel; sourced by the
# comfort profile (/etc/pengyos/shell/profile).
define ZSH_AUTOSUGGESTIONS_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/share/zsh/plugins
	cp -r $(@D) $(TARGET_DIR)/usr/share/zsh/plugins/zsh-autosuggestions
endef

$(eval $(generic-package))
