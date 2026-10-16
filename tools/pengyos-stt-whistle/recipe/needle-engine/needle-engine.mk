################################################################################
#
# needle-engine — the Cactus inference engine that runs Whistle.
#
# Lane 2 (SPEC §2.5): a vendor's own prebuilt binary, pinned by hash, installed
# under an isolated prefix. There is no source for this engine; the divergence
# is recorded in ../../../../divergence.toml and ends when upstream publishes
# the engine source (see the entry's `expiry`).
#
# The binary is self-contained: it needs only libc, libm, pthread and dl, and
# makes no network syscalls at load or transcribe time (verified with strace).
# It carries its own C++ runtime, so no libc++ is required on the target.
#
################################################################################

NEEDLE_ENGINE_VERSION = 3.3.1
NEEDLE_ENGINE_SITE = https://huggingface.co/Cactus-Compute/needle3/resolve/main/linux-$(BR2_ARCH)
NEEDLE_ENGINE_SOURCE = needle
NEEDLE_ENGINE_SOURCES = needle needle.h
NEEDLE_ENGINE_LICENSE = Apache-2.0
NEEDLE_ENGINE_LICENSE_FILES = needle.h
NEEDLE_ENGINE_INSTALL_STAGING = NO
NEEDLE_ENGINE_INSTALL_TARGET = YES

# Upstream publishes linux-x86_64, linux-arm64, linux-armv7, linux-riscv64 and
# linux-mipsel. Refuse to build anywhere else rather than fetch nothing.
ifeq ($(BR2_PACKAGE_NEEDLE_ENGINE_ARCH_OK),y)
define NEEDLE_ENGINE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/needle \
		$(TARGET_DIR)/usr/lib/pengyos/stt/needle
endef
else
define NEEDLE_ENGINE_INSTALL_TARGET_CMDS
	echo "needle-engine: upstream publishes no linux-$(BR2_ARCH) build" >&2; \
	exit 1
endef
endif

$(eval $(generic-package))
