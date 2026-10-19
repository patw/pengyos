################################################################################
#
# font-noto-symbols2
#
################################################################################

FONT_NOTO_SYMBOLS2_VERSION = 2.008
FONT_NOTO_SYMBOLS2_SOURCE = NotoSansSymbols2-v$(FONT_NOTO_SYMBOLS2_VERSION).zip
FONT_NOTO_SYMBOLS2_SITE = https://github.com/notofonts/symbols/releases/download/NotoSansSymbols2-v$(FONT_NOTO_SYMBOLS2_VERSION)
FONT_NOTO_SYMBOLS2_LICENSE = OFL-1.1
FONT_NOTO_SYMBOLS2_LICENSE_FILES = OFL.txt

define FONT_NOTO_SYMBOLS2_EXTRACT_CMDS
	$(UNZIP) $(FONT_NOTO_SYMBOLS2_DL_DIR)/$(FONT_NOTO_SYMBOLS2_SOURCE) -d $(@D)
endef

define FONT_NOTO_SYMBOLS2_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/NotoSansSymbols2/hinted/ttf/NotoSansSymbols2-Regular.ttf \
		$(TARGET_DIR)/usr/share/fonts/noto/NotoSansSymbols2-Regular.ttf
endef

$(eval $(generic-package))
