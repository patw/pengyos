################################################################################
#
# nvidia-container-toolkit
#
################################################################################

NVIDIA_CONTAINER_TOOLKIT_VERSION = 1.20.1
NVIDIA_CONTAINER_TOOLKIT_SITE = $(call github,NVIDIA,nvidia-container-toolkit,v$(NVIDIA_CONTAINER_TOOLKIT_VERSION))
NVIDIA_CONTAINER_TOOLKIT_LICENSE = Apache-2.0
NVIDIA_CONTAINER_TOOLKIT_LICENSE_FILES = LICENSE
NVIDIA_CONTAINER_TOOLKIT_GOMOD = github.com/NVIDIA/nvidia-container-toolkit
# CDI only: nvidia-ctk generates the spec, nvidia-cdi-hook is what the spec's
# hooks run (container ldcache, symlinks). Podman reads CDI natively, so the
# runtime and the legacy OCI hook are left out.
NVIDIA_CONTAINER_TOOLKIT_BUILD_TARGETS = cmd/nvidia-ctk cmd/nvidia-cdi-hook
NVIDIA_CONTAINER_TOOLKIT_LDFLAGS = \
	-X $(NVIDIA_CONTAINER_TOOLKIT_GOMOD)/internal/info.version=$(NVIDIA_CONTAINER_TOOLKIT_VERSION)
# go-nvml is cgo that dlopen()s libnvidia-ml at run time; upstream's Makefile
# links it with these flags so its symbols stay unresolved until then.
NVIDIA_CONTAINER_TOOLKIT_EXTLDFLAGS = \
	-Wl,--export-dynamic -Wl,--unresolved-symbols=ignore-in-object-files -Wl,-z,lazy

# Upstream's refresh unit and udev rule: the spec is (re)written each time the
# nvidia module loads, so a machine without an NVIDIA GPU, or a VM, never runs
# it. Upstream's .path unit (refresh after a driver package upgrade) is left
# out: the driver here changes only with the read-only image. The unit's
# [Install] section (WantedBy=multi-user.target) goes too: systemd's first-boot
# preset enables every unit that has one, so it ran at every boot and failed
# on machines and VMs without an NVIDIA GPU (26.10.5's first QEMU check).
define NVIDIA_CONTAINER_TOOLKIT_INSTALL_UNITS
	$(INSTALL) -D -m 0644 $(@D)/deployments/systemd/nvidia-cdi-refresh.service \
		$(TARGET_DIR)/usr/lib/systemd/system/nvidia-cdi-refresh.service
	sed -i '/^\[Install\]/,$$d' $(TARGET_DIR)/usr/lib/systemd/system/nvidia-cdi-refresh.service
	$(INSTALL) -D -m 0644 $(@D)/deployments/udev/99-nvidia-cdi-refresh.rules \
		$(TARGET_DIR)/usr/lib/udev/rules.d/99-nvidia-cdi-refresh.rules
endef
NVIDIA_CONTAINER_TOOLKIT_POST_INSTALL_TARGET_HOOKS += NVIDIA_CONTAINER_TOOLKIT_INSTALL_UNITS

$(eval $(golang-package))
