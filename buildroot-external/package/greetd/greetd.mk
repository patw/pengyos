################################################################################
#
# greetd
#
################################################################################

GREETD_VERSION = 0.10.3
GREETD_SOURCE = $(GREETD_VERSION).tar.gz
GREETD_SITE = https://github.com/kennylevinsen/greetd/archive/refs/tags
GREETD_LICENSE = GPL-3.0-only
GREETD_LICENSE_FILES = LICENSE
# greetd links libpam (pam-sys); without this the link fails with
# "cannot find -lpam".
GREETD_DEPENDENCIES = linux-pam

# The greetd repo root is a *virtual* (workspace) manifest, so the default
# `cargo install --path ./` fails with "found a virtual manifest ... instead of a
# package manifest". Provide an explicit install that copies the two workspace
# binaries we ship: greetd itself and agreety (its bundled console greeter).
# Defining <pkg>_INSTALL_TARGET_CMDS makes pkg-cargo.mk skip its own (
# `ifndef $(2)_INSTALL_TARGET_CMDS`).
define GREETD_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/target/$(RUSTC_TARGET_NAME)/release/greetd \
		$(TARGET_DIR)/usr/bin/greetd
	$(INSTALL) -D -m 0755 $(@D)/target/$(RUSTC_TARGET_NAME)/release/agreety \
		$(TARGET_DIR)/usr/bin/agreety
endef

$(eval $(cargo-package))
