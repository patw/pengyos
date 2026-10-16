################################################################################
#
# laya-model — Laya System One decision weights (Apache-2.0)
#
# PROTOTYPE SKELETON — not installed. Pinned data, sha256-verified, downloaded
# over https (what pengyos-recipe-lint's no_unverified_downloads rule wants).
#
################################################################################

LAYA_MODEL_VERSION = 2026-10-02
LAYA_MODEL_SITE = https://huggingface.co/ggml-org/Laya-GGUF/resolve/main
LAYA_MODEL_SOURCE = Laya-Q8_0.gguf
LAYA_MODEL_LICENSE = Apache-2.0
LAYA_MODEL_LICENSE_FILES = LICENSE

define LAYA_MODEL_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/Laya-Q8_0.gguf \
		$(TARGET_DIR)/usr/share/pengyos/jev/Laya-Q8_0.gguf
endef

$(eval $(generic-package))
