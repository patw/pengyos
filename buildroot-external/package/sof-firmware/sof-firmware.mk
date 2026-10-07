################################################################################
#
# sof-firmware
#
################################################################################

SOF_FIRMWARE_VERSION = 2026.09.1
SOF_FIRMWARE_SOURCE = sof-bin-$(SOF_FIRMWARE_VERSION).tar.gz
SOF_FIRMWARE_SITE = https://github.com/thesofproject/sof-bin/releases/download/v$(SOF_FIRMWARE_VERSION)
SOF_FIRMWARE_LICENSE = BSD-3-Clause (Intel firmware and topologies)
SOF_FIRMWARE_LICENSE_FILES = LICENCE.Intel
SOF_FIRMWARE_REDISTRIBUTE = YES

# What upstream's install.sh does: the sof* directories (firmware, IPC4
# firmware and libraries, topologies; some are symlinks) into
# /lib/firmware/intel. The NXP directories are for i.MX, not x86.
define SOF_FIRMWARE_INSTALL_TARGET_CMDS
	mkdir -p $(TARGET_DIR)/lib/firmware/intel
	cd $(@D) && for d in sof sof-tplg sof-ace-tplg sof-ipc4 sof-ipc4-lib sof-ipc4-tplg; do \
		[ -e "$$d" ] || continue; \
		rm -rf $(TARGET_DIR)/lib/firmware/intel/$$d; \
		cp -a $$d $(TARGET_DIR)/lib/firmware/intel/; \
	done
endef

$(eval $(generic-package))
