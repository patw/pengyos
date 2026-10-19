################################################################################
#
# font-symbols-nerd
#
################################################################################

FONT_SYMBOLS_NERD_VERSION = 3.5.1
FONT_SYMBOLS_NERD_SOURCE = NerdFontsSymbolsOnly.tar.xz
FONT_SYMBOLS_NERD_SITE = https://github.com/ryanoasis/nerd-fonts/releases/download/v$(FONT_SYMBOLS_NERD_VERSION)
FONT_SYMBOLS_NERD_LICENSE = MIT
FONT_SYMBOLS_NERD_LICENSE_FILES = LICENSE

# The release tarball has no top-level directory.
FONT_SYMBOLS_NERD_STRIP_COMPONENTS = 0

define FONT_SYMBOLS_NERD_INSTALL_TARGET_CMDS
	$(INSTALL) -d $(TARGET_DIR)/usr/share/fonts/symbols-nerd
	$(INSTALL) -m 0644 $(@D)/SymbolsNerdFont-Regular.ttf \
		$(@D)/SymbolsNerdFontMono-Regular.ttf \
		$(TARGET_DIR)/usr/share/fonts/symbols-nerd/
endef

$(eval $(generic-package))
