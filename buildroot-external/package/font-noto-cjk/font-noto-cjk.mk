################################################################################
#
# font-noto-cjk
#
################################################################################

FONT_NOTO_CJK_VERSION = 2.004
FONT_NOTO_CJK_SOURCE = NotoSansCJK-VF.otf.ttc
FONT_NOTO_CJK_SITE = https://github.com/notofonts/noto-cjk/raw/Sans$(FONT_NOTO_CJK_VERSION)/Sans/Variable/OTC
FONT_NOTO_CJK_LICENSE = OFL-1.1
FONT_NOTO_CJK_REDISTRIBUTE = YES

# A single font file, not an archive.
define FONT_NOTO_CJK_EXTRACT_CMDS
	cp $(FONT_NOTO_CJK_DL_DIR)/$(FONT_NOTO_CJK_SOURCE) $(@D)/
endef

define FONT_NOTO_CJK_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/$(FONT_NOTO_CJK_SOURCE) \
		$(TARGET_DIR)/usr/share/fonts/noto-cjk/$(FONT_NOTO_CJK_SOURCE)
endef

$(eval $(generic-package))
