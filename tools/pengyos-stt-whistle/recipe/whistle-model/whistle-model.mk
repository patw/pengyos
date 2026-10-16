################################################################################
#
# whistle-model — the Cactus Whistle speech-to-text weights (16.9 MB).
#
# Data, not code: Apache-2.0 weights, pinned by hash, installed to
# /usr/share/pengyos/stt. The safetensors checkpoint is published alongside it,
# so the quantised .cact can be rebuilt from the published checkpoint.
#
################################################################################

WHISTLE_MODEL_VERSION = 2.0.0
WHISTLE_MODEL_SITE = https://huggingface.co/Cactus-Compute/whistle/resolve/main
WHISTLE_MODEL_SOURCE = whistle.cact
WHISTLE_MODEL_LICENSE = Apache-2.0
WHISTLE_MODEL_LICENSE_FILES = LICENSE
# The license text is a second pinned download, so the license scan has it.
WHISTLE_MODEL_SOURCES = whistle.cact LICENSE
WHISTLE_MODEL_INSTALL_STAGING = NO
WHISTLE_MODEL_INSTALL_TARGET = YES

define WHISTLE_MODEL_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/share/pengyos/stt
	$(INSTALL) -D -m 0644 $(@D)/whistle.cact \
		$(TARGET_DIR)/usr/share/pengyos/stt/whistle.cact
endef

$(eval $(generic-package))
