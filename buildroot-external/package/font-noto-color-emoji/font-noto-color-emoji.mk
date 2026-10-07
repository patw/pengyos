################################################################################
#
# font-noto-color-emoji
#
################################################################################

# The single font file from the release tag (the source tree is hundreds of MB
# of PNGs and tooling).
FONT_NOTO_COLOR_EMOJI_VERSION = 2.051
FONT_NOTO_COLOR_EMOJI_SITE = https://raw.githubusercontent.com/googlefonts/noto-emoji/v$(FONT_NOTO_COLOR_EMOJI_VERSION)/fonts
FONT_NOTO_COLOR_EMOJI_SOURCE = NotoColorEmoji.ttf
FONT_NOTO_COLOR_EMOJI_EXTRA_DOWNLOADS = $(FONT_NOTO_COLOR_EMOJI_SITE)/LICENSE
FONT_NOTO_COLOR_EMOJI_LICENSE = OFL-1.1
FONT_NOTO_COLOR_EMOJI_LICENSE_FILES = LICENSE

define FONT_NOTO_COLOR_EMOJI_EXTRACT_CMDS
	cp $(FONT_NOTO_COLOR_EMOJI_DL_DIR)/NotoColorEmoji.ttf \
		$(FONT_NOTO_COLOR_EMOJI_DL_DIR)/LICENSE $(@D)/
endef

define FONT_NOTO_COLOR_EMOJI_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/NotoColorEmoji.ttf \
		$(TARGET_DIR)/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf
endef

$(eval $(generic-package))
