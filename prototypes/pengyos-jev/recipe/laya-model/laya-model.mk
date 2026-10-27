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
# The repository has no LICENSE file; the model card's front matter is where
# upstream states the license (`license: apache-2.0`), so that is the file the
# license scan keeps.
LAYA_MODEL_EXTRA_DOWNLOADS = README.md
LAYA_MODEL_LICENSE_FILES = README.md

# Bare files, not an archive: copy them into the build dir.
define LAYA_MODEL_EXTRACT_CMDS
	cp $(LAYA_MODEL_DL_DIR)/Laya-Q8_0.gguf $(LAYA_MODEL_DL_DIR)/README.md $(@D)/
endef

define LAYA_MODEL_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/Laya-Q8_0.gguf \
		$(TARGET_DIR)/usr/share/pengyos/jev/Laya-Q8_0.gguf
endef

$(eval $(generic-package))
