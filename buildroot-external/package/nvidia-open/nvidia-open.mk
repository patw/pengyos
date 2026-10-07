################################################################################
#
# nvidia-open
#
################################################################################

# One .run carries both halves: kernel-open/ (the open kernel modules, with the
# prebuilt nv-kernel/nv-modeset cores) and the userspace + GSP firmware of the
# same release. Building both from it keeps them in lockstep -- a module/userspace
# version mismatch is a dead GPU. Blackwell (RTX 50) needs >= 570; 595 is the
# production branch and builds against our 7.2 kernel.
NVIDIA_OPEN_VERSION = 595.104.02
NVIDIA_OPEN_SITE = https://download.nvidia.com/XFree86/Linux-x86_64/$(NVIDIA_OPEN_VERSION)
NVIDIA_OPEN_SOURCE = NVIDIA-Linux-x86_64-$(NVIDIA_OPEN_VERSION).run
NVIDIA_OPEN_LICENSE = NVIDIA Software License (userspace, GSP firmware), MIT or GPL-2.0 (kernel modules)
NVIDIA_OPEN_LICENSE_FILES = LICENSE
NVIDIA_OPEN_REDISTRIBUTE = NO
NVIDIA_OPEN_DEPENDENCIES =

# The .run is a self-extracting script, and it will not extract into an
# existing (even empty) directory.
define NVIDIA_OPEN_EXTRACT_CMDS
	$(SHELL) $(NVIDIA_OPEN_DL_DIR)/$(NVIDIA_OPEN_SOURCE) --extract-only \
		--target $(@D)/tmp-extract
	chmod u+w -R $(@D)
	mv $(@D)/tmp-extract/* $(@D)/tmp-extract/.manifest $(@D)
	rm -rf $(@D)/tmp-extract
endef

# --- Kernel modules ------------------------------------------------------------
# nvidia-peermem needs InfiniBand; the other four are the whole driver.
NVIDIA_OPEN_MODULE_SUBDIRS = kernel-open
NVIDIA_OPEN_MODULE_MAKE_OPTS = \
	IGNORE_CC_MISMATCH=1 \
	NV_KERNEL_SOURCES="$(LINUX_DIR)" \
	NV_KERNEL_OUTPUT="$(LINUX_DIR)" \
	NV_KERNEL_MODULES="nvidia nvidia-modeset nvidia-drm nvidia-uvm"

# --- Userspace -----------------------------------------------------------------
# Compute and video, in every NVIDIA image: CUDA (inference), NVML (nvidia-smi,
# monitoring), NVENC / NVDEC / optical flow (encoding; ffmpeg dlopens these by
# SONAME). They need nothing but glibc.
NVIDIA_OPEN_LIBS = \
	libcuda.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-ptxjitcompiler.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-nvvm.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-nvvm70.so.4 \
	libnvidia-ml.so.$(NVIDIA_OPEN_VERSION) \
	libnvcuvid.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-encode.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-opticalflow.so.$(NVIDIA_OPEN_VERSION)

# Graphics: EGL/GLES + GBM + the Wayland EGL platforms, what cosmic-comp needs
# to render and scan out. No Xorg driver (libglxserver_nvidia): the Desktop's
# only X server is Xwayland, which renders through GBM/EGL (glamor).
ifeq ($(BR2_PACKAGE_NVIDIA_OPEN_GRAPHICS),y)
NVIDIA_OPEN_DEPENDENCIES += libglvnd libdrm mesa3d wayland
NVIDIA_OPEN_LIBS += \
	libEGL_nvidia.so.$(NVIDIA_OPEN_VERSION) \
	libGLESv1_CM_nvidia.so.$(NVIDIA_OPEN_VERSION) \
	libGLESv2_nvidia.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-eglcore.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-glcore.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-glsi.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-tls.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-gpucomp.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-glvkspirv.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-allocator.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-egl-gbm.so.1.1.3 \
	libnvidia-egl-wayland.so.1.1.20 \
	libnvidia-egl-wayland2.so.1.0.1
define NVIDIA_OPEN_INSTALL_GRAPHICS
	# GBM backend: Mesa's libgbm loads <drm driver name>_gbm.so.
	mkdir -p $(TARGET_DIR)/usr/lib/gbm
	ln -sf ../libnvidia-allocator.so.1 $(TARGET_DIR)/usr/lib/gbm/nvidia-drm_gbm.so
	# glvnd EGL vendor + EGL external platforms (GBM, Wayland).
	$(INSTALL) -D -m 0644 $(@D)/10_nvidia.json \
		$(TARGET_DIR)/usr/share/glvnd/egl_vendor.d/10_nvidia.json
	$(foreach f,09_nvidia_wayland2.json 10_nvidia_wayland.json 15_nvidia_gbm.json, \
		$(INSTALL) -D -m 0644 $(@D)/$(f) \
			$(TARGET_DIR)/usr/share/egl/egl_external_platform.d/$(f)$(sep))
	$(INSTALL) -D -m 0644 $(@D)/nvidia-application-profiles-$(NVIDIA_OPEN_VERSION)-rc \
		$(TARGET_DIR)/usr/share/nvidia/nvidia-application-profiles-$(NVIDIA_OPEN_VERSION)-rc
	$(INSTALL) -D -m 0644 \
		$(@D)/nvidia-application-profiles-$(NVIDIA_OPEN_VERSION)-key-documentation \
		$(TARGET_DIR)/usr/share/nvidia/nvidia-application-profiles-$(NVIDIA_OPEN_VERSION)-key-documentation
	$(INSTALL) -D -m 0755 $(NVIDIA_OPEN_PKGDIR)/prime-run $(TARGET_DIR)/usr/bin/prime-run
endef
endif

# Vulkan: the ICD is libGLX_nvidia (also the glvnd GLX vendor, for OpenGL apps
# under Xwayland), which links libX11/libXext. The X11 EGL platforms (xcb, xlib)
# serve EGL apps under Xwayland.
ifeq ($(BR2_PACKAGE_NVIDIA_OPEN_VULKAN),y)
NVIDIA_OPEN_DEPENDENCIES += vulkan-loader xlib_libX11 xlib_libXext
NVIDIA_OPEN_LIBS += \
	libGLX_nvidia.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-rtcore.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-present.so.$(NVIDIA_OPEN_VERSION) \
	libnvidia-egl-xcb.so.1.0.5 \
	libnvidia-egl-xlib.so.1.0.5
define NVIDIA_OPEN_INSTALL_VULKAN
	$(INSTALL) -D -m 0644 $(@D)/nvidia_icd.json \
		$(TARGET_DIR)/usr/share/vulkan/icd.d/nvidia_icd.json
	$(INSTALL) -D -m 0644 $(@D)/nvidia_layers.json \
		$(TARGET_DIR)/usr/share/vulkan/implicit_layer.d/nvidia_layers.json
	$(foreach f,20_nvidia_xcb.json 20_nvidia_xlib.json, \
		$(INSTALL) -D -m 0644 $(@D)/$(f) \
			$(TARGET_DIR)/usr/share/egl/egl_external_platform.d/$(f)$(sep))
endef
endif

# Install a library plus its SONAME link (what the loader and dlopen() use).
define NVIDIA_OPEN_INSTALL_LIB
	$(INSTALL) -D -m 0755 $(@D)/$(1) $(TARGET_DIR)/usr/lib/$(1)
	soname="$$($(TARGET_READELF) -d $(@D)/$(1) \
		| sed -r -e '/.*\(SONAME\).*\[(.*)\]$$/!d; s//\1/;')"; \
	if [ -n "$${soname}" -a "$${soname}" != "$(1)" ]; then \
		ln -sf $(1) $(TARGET_DIR)/usr/lib/$${soname}; \
	fi
endef

NVIDIA_OPEN_PROGS = nvidia-smi nvidia-modprobe nvidia-persistenced

NVIDIA_OPEN_SUSPEND_UNITS = \
	nvidia-suspend.service \
	nvidia-hibernate.service \
	nvidia-resume.service \
	nvidia-suspend-then-hibernate.service

define NVIDIA_OPEN_INSTALL_TARGET_CMDS
	$(foreach lib,$(NVIDIA_OPEN_LIBS),$(call NVIDIA_OPEN_INSTALL_LIB,$(lib))$(sep))
	ln -sf libcuda.so.1 $(TARGET_DIR)/usr/lib/libcuda.so
	$(NVIDIA_OPEN_INSTALL_GRAPHICS)
	$(NVIDIA_OPEN_INSTALL_VULKAN)
	# GSP firmware: the open modules always run the GPU through GSP.
	$(INSTALL) -D -m 0644 -t $(TARGET_DIR)/lib/firmware/nvidia/$(NVIDIA_OPEN_VERSION) \
		$(@D)/firmware/gsp_ga10x.bin $(@D)/firmware/gsp_tu10x.bin
	$(foreach p,$(NVIDIA_OPEN_PROGS), \
		$(INSTALL) -D -m 0755 $(@D)/$(p) $(TARGET_DIR)/usr/bin/$(p)$(sep))
	# The MPS control daemon and server: several CUDA processes share one GPU.
	$(foreach p,nvidia-cuda-mps-control nvidia-cuda-mps-server, \
		$(INSTALL) -D -m 0755 $(@D)/$(p) $(TARGET_DIR)/usr/bin/$(p)$(sep))
	# Module options (nouveau blacklist, nvidia-drm load order) + runtime PM.
	$(INSTALL) -D -m 0644 $(NVIDIA_OPEN_PKGDIR)/nvidia.conf \
		$(TARGET_DIR)/usr/lib/modprobe.d/nvidia.conf
	$(INSTALL) -D -m 0644 $(NVIDIA_OPEN_PKGDIR)/80-nvidia-pm.rules \
		$(TARGET_DIR)/usr/lib/udev/rules.d/80-nvidia-pm.rules
endef

# Suspend/resume: /proc/driver/nvidia/suspend hooks (needed for CUDA/UVM state
# across sleep). Enabled statically so an immutable /usr carries the wiring.
define NVIDIA_OPEN_INSTALL_INIT_SYSTEMD
	$(foreach u,$(NVIDIA_OPEN_SUSPEND_UNITS), \
		$(INSTALL) -D -m 0644 $(@D)/systemd/system/$(u) \
			$(TARGET_DIR)/usr/lib/systemd/system/$(u)$(sep))
	$(INSTALL) -D -m 0755 $(@D)/systemd/nvidia-sleep.sh \
		$(TARGET_DIR)/usr/bin/nvidia-sleep.sh
	$(INSTALL) -D -m 0755 $(@D)/systemd/system-sleep/nvidia \
		$(TARGET_DIR)/usr/lib/systemd/system-sleep/nvidia
	for s in suspend hibernate suspend-then-hibernate; do \
		mkdir -p $(TARGET_DIR)/usr/lib/systemd/system/systemd-$$s.service.wants; \
		ln -sf ../nvidia-$$s.service \
			$(TARGET_DIR)/usr/lib/systemd/system/systemd-$$s.service.wants/nvidia-$$s.service; \
		ln -sf ../nvidia-resume.service \
			$(TARGET_DIR)/usr/lib/systemd/system/systemd-$$s.service.wants/nvidia-resume.service; \
	done
	$(NVIDIA_OPEN_INSTALL_PERSISTENCED)
endef

# nvidia-persistenced for headless GPUs: started by udev when the nvidia module
# loads (so a machine without an NVIDIA GPU, or a VM, never runs it).
ifeq ($(BR2_PACKAGE_NVIDIA_OPEN_PERSISTENCED),y)
define NVIDIA_OPEN_INSTALL_PERSISTENCED
	$(INSTALL) -D -m 0644 $(NVIDIA_OPEN_PKGDIR)/nvidia-persistenced.service \
		$(TARGET_DIR)/usr/lib/systemd/system/nvidia-persistenced.service
	$(INSTALL) -D -m 0644 $(NVIDIA_OPEN_PKGDIR)/71-nvidia-persistenced.rules \
		$(TARGET_DIR)/usr/lib/udev/rules.d/71-nvidia-persistenced.rules
endef
endif

# nvidia-modprobe creates /dev/nvidia* and loads nvidia-uvm on demand for an
# unprivileged CUDA/EGL client; NVIDIA ships it setuid for exactly that.
define NVIDIA_OPEN_PERMISSIONS
	/usr/bin/nvidia-modprobe f 4755 0 0 - - - - -
endef

$(eval $(kernel-module))
$(eval $(generic-package))
