################################################################################
#
# pengy
#
################################################################################

PENGY_VERSION = 1.11.5
PENGY_SITE = $(call github,patw,PengyR,v$(PENGY_VERSION))
PENGY_LICENSE = MIT
PENGY_LICENSE_FILES = LICENSE

# One workspace: pengy_core (lib + staticlib), pengy-cli, pengy-web; the Qt6
# GUI (gui/, CMake) links the pengy_core staticlib. Build only what is enabled.
PENGY_CARGO_CRATES = \
	$(if $(BR2_PACKAGE_PENGY_CLI),pengy-cli) \
	$(if $(BR2_PACKAGE_PENGY_WEB),pengy-web) \
	$(if $(BR2_PACKAGE_PENGY_GUI),pengy_core)
PENGY_CARGO_BUILD_OPTS = $(foreach c,$(PENGY_CARGO_CRATES),-p $(c))
PENGY_RUST_OUT = $(@D)/target/$(RUSTC_TARGET_NAME)/release

ifeq ($(BR2_PACKAGE_PENGY_GUI),y)
PENGY_DEPENDENCIES += host-cmake qt6base qt6svg openssl
define PENGY_BUILD_GUI
	$(TARGET_MAKE_ENV) $(BR2_CMAKE) -S $(@D)/gui -B $(@D)/gui/build \
		-DCMAKE_TOOLCHAIN_FILE="$(HOST_DIR)/share/buildroot/toolchainfile.cmake" \
		-DCMAKE_BUILD_TYPE=Release \
		-DQT_HOST_PATH=$(HOST_DIR) \
		-DRUST_TARGET_DIR=$(PENGY_RUST_OUT)
	$(TARGET_MAKE_ENV) $(BR2_CMAKE) --build $(@D)/gui/build -j$(PARALLEL_JOBS)
endef
PENGY_POST_BUILD_HOOKS += PENGY_BUILD_GUI

define PENGY_INSTALL_GUI
	$(INSTALL) -D -m 0755 $(@D)/gui/build/pengy $(TARGET_DIR)/usr/bin/pengy
	$(INSTALL) -D -m 0644 $(PENGY_PKGDIR)/pengy.desktop \
		$(TARGET_DIR)/usr/share/applications/pengy.desktop
	$(INSTALL) -D -m 0644 $(PENGY_PKGDIR)/pengy.png \
		$(TARGET_DIR)/usr/share/icons/hicolor/256x256/apps/pengy.png
endef
endif

define PENGY_INSTALL_TARGET_CMDS
	$(if $(BR2_PACKAGE_PENGY_CLI),$(INSTALL) -D -m 0755 $(PENGY_RUST_OUT)/pengy-cli \
		$(TARGET_DIR)/usr/bin/pengy-cli)
	$(if $(BR2_PACKAGE_PENGY_WEB),$(INSTALL) -D -m 0755 $(PENGY_RUST_OUT)/pengy-web \
		$(TARGET_DIR)/usr/bin/pengy-web)
	$(PENGY_INSTALL_GUI)
endef

$(eval $(cargo-package))
