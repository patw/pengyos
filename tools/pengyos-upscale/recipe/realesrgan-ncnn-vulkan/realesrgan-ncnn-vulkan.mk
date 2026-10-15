################################################################################
#
# realesrgan-ncnn-vulkan - Real-ESRGAN's ncnn/Vulkan image upscaler.
#
# Lane 2 (SPEC 2.5): the vendor's own prebuilt binary, pinned by hash,
# installed under an isolated prefix (/usr/lib/pengyos/upscale). There is no
# Buildroot recipe for ncnn in 2026.08 and no target glslang, so a Lane 1
# build is not available today - see recipe/README.md for the measurement and
# the cost of authoring it.
#
# The recipe installs the whole feature, not just the binary: the
# pengyos-upscale command, the COSMIC Files right-click actions and the
# "Open with" entry. That is deliberate - the menu entries exist exactly when
# the engine exists, so a Desktop image with the package off has no menu
# entry pointing at a program that is not there.
#
################################################################################

REALESRGAN_NCNN_VULKAN_VERSION = 0.2.5.0
REALESRGAN_NCNN_VULKAN_SITE = https://github.com/xinntao/Real-ESRGAN/releases/download/v$(REALESRGAN_NCNN_VULKAN_VERSION)
REALESRGAN_NCNN_VULKAN_SOURCE = realesrgan-ncnn-vulkan-20220424-ubuntu.zip
# The release archive carries no license text, so the license scan has its own
# pinned copy from the same tag.
REALESRGAN_NCNN_VULKAN_EXTRA_DOWNLOADS = https://raw.githubusercontent.com/xinntao/Real-ESRGAN/v$(REALESRGAN_NCNN_VULKAN_VERSION)/LICENSE
REALESRGAN_NCNN_VULKAN_LICENSE = BSD-3-Clause
REALESRGAN_NCNN_VULKAN_LICENSE_FILES = LICENSE
REALESRGAN_NCNN_VULKAN_INSTALL_STAGING = NO
REALESRGAN_NCNN_VULKAN_INSTALL_TARGET = YES

# The vendor binary is built with OpenMP, so it links libgomp.so.1 (measured
# with ldd: libc, libgcc_s, libgomp, libm, libmvec, libvulkan). Buildroot's
# target ships every one of those except libgomp; board/desktop/post-build.sh
# copies the host's libgomp.so.1 into the image, the same way it already
# copies the six other runtime libs the host-built binaries need. Without it
# the engine does not start.

define REALESRGAN_NCNN_VULKAN_COPY_LICENSE
	$(INSTALL) -D -m 0644 $(REALESRGAN_NCNN_VULKAN_DL_DIR)/LICENSE \
		$(@D)/LICENSE
endef
REALESRGAN_NCNN_VULKAN_POST_EXTRACT_HOOKS += REALESRGAN_NCNN_VULKAN_COPY_LICENSE

# Which model graphs to install. The archive carries all of them; the image
# ships only what the config selects, because the photo model alone is 33 MB.
REALESRGAN_NCNN_VULKAN_MODELS =
ifeq ($(BR2_PACKAGE_REALESRGAN_NCNN_VULKAN_MODEL_FAST),y)
REALESRGAN_NCNN_VULKAN_MODELS += realesr-animevideov3-x2 realesr-animevideov3-x3 realesr-animevideov3-x4
endif
ifeq ($(BR2_PACKAGE_REALESRGAN_NCNN_VULKAN_MODEL_ANIME),y)
REALESRGAN_NCNN_VULKAN_MODELS += realesrgan-x4plus-anime
endif
ifeq ($(BR2_PACKAGE_REALESRGAN_NCNN_VULKAN_MODEL_PHOTO),y)
REALESRGAN_NCNN_VULKAN_MODELS += realesrgan-x4plus
endif

define REALESRGAN_NCNN_VULKAN_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/usr/lib/pengyos/upscale
	$(INSTALL) -D -m 0755 $(@D)/realesrgan-ncnn-vulkan \
		$(TARGET_DIR)/usr/lib/pengyos/upscale/realesrgan-ncnn-vulkan
	mkdir -p $(TARGET_DIR)/usr/share/pengyos/upscale/models
	$(foreach m,$(REALESRGAN_NCNN_VULKAN_MODELS), \
		$(INSTALL) -D -m 0644 $(@D)/models/$(m).param \
			$(TARGET_DIR)/usr/share/pengyos/upscale/models/$(m).param; \
		$(INSTALL) -D -m 0644 $(@D)/models/$(m).bin \
			$(TARGET_DIR)/usr/share/pengyos/upscale/models/$(m).bin; \
	)
	# The command, the right-click actions and the "Open with" entry.
	$(INSTALL) -D -m 0755 $(REALESRGAN_NCNN_VULKAN_PKGDIR)/pengyos-upscale \
		$(TARGET_DIR)/usr/bin/pengyos-upscale
	$(INSTALL) -D -m 0644 $(REALESRGAN_NCNN_VULKAN_PKGDIR)/context_actions \
		$(TARGET_DIR)/usr/share/cosmic/com.system76.CosmicFiles/v1/context_actions
	$(INSTALL) -D -m 0644 $(REALESRGAN_NCNN_VULKAN_PKGDIR)/io.catbee.pengyos.Upscale.desktop \
		$(TARGET_DIR)/usr/share/applications/io.catbee.pengyos.Upscale.desktop
endef

$(eval $(generic-package))
