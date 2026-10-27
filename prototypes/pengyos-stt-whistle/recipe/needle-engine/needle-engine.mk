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
# Upstream names its directories after the CPU, not Buildroot's BR2_ARCH
# (aarch64 -> arm64, arm -> armv7, mipsel stays mipsel).
NEEDLE_ENGINE_ARCH = $(BR2_ARCH)
ifeq ($(BR2_aarch64),y)
NEEDLE_ENGINE_ARCH = arm64
else ifeq ($(BR2_arm),y)
NEEDLE_ENGINE_ARCH = armv7
endif
NEEDLE_ENGINE_SITE = https://huggingface.co/Cactus-Compute/needle3/resolve/main/linux-$(NEEDLE_ENGINE_ARCH)
NEEDLE_ENGINE_SOURCE = needle
# The header (for pengyos-say.c) is a second download from the same directory;
# the license text sits at the repository root (needle.h carries none).
NEEDLE_ENGINE_EXTRA_DOWNLOADS = \
	needle.h \
	https://huggingface.co/Cactus-Compute/needle3/resolve/main/LICENSE
NEEDLE_ENGINE_LICENSE = Apache-2.0
NEEDLE_ENGINE_LICENSE_FILES = LICENSE
NEEDLE_ENGINE_INSTALL_STAGING = NO
NEEDLE_ENGINE_INSTALL_TARGET = YES

# The downloads are bare files, not archives, and Buildroot's generic extract
# only unpacks tar (and its compressors): copy them into the build dir.
define NEEDLE_ENGINE_EXTRACT_CMDS
	cp $(NEEDLE_ENGINE_DL_DIR)/needle $(NEEDLE_ENGINE_DL_DIR)/needle.h \
		$(NEEDLE_ENGINE_DL_DIR)/LICENSE $(@D)/
endef

# Upstream publishes linux-x86_64, linux-arm64, linux-armv7, linux-riscv64 and
# linux-mipsel. Refuse to build anywhere else rather than fetch nothing.
ifeq ($(BR2_PACKAGE_NEEDLE_ENGINE_ARCH_OK),y)
define NEEDLE_ENGINE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/needle \
		$(TARGET_DIR)/usr/lib/pengyos/stt/needle
endef
else
define NEEDLE_ENGINE_INSTALL_TARGET_CMDS
	echo "needle-engine: upstream publishes no linux-$(NEEDLE_ENGINE_ARCH) build" >&2; \
	exit 1
endef
endif

$(eval $(generic-package))
