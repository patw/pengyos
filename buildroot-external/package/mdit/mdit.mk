################################################################################
#
# mdit
#
# A Qt6 Widgets Markdown editor (ours, MIT). Its own `cmake --install` puts the
# binary, the hicolor icons and mdit.desktop in place. Built against the
# image's Qt6 with the host Qt tools (moc/rcc/uic); its QtTest suite is not
# cross-built.
#
################################################################################

MDIT_VERSION = 0.1.2
MDIT_SITE = $(call github,patw,mdit,v$(MDIT_VERSION))
MDIT_LICENSE = MIT
MDIT_LICENSE_FILES = LICENSE
MDIT_DEPENDENCIES = qt6base
MDIT_CONF_OPTS = \
	-DQT_HOST_PATH=$(HOST_DIR) \
	-DBUILD_TESTS=OFF

$(eval $(cmake-package))
