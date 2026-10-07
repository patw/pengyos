################################################################################
#
# cutecosmic
#
################################################################################

CUTECOSMIC_VERSION = 0.2.0
CUTECOSMIC_SITE = $(call github,IgKh,cutecosmic,v$(CUTECOSMIC_VERSION))
CUTECOSMIC_LICENSE = GPL-3.0+
CUTECOSMIC_LICENSE_FILES = COPYING
# The Rust bindings (libcosmic) live in bindings/: vendor and build that crate.
CUTECOSMIC_SUBDIR = bindings
CUTECOSMIC_DEPENDENCIES = host-cmake qt6base

CUTECOSMIC_BINDINGS_OUT = $(@D)/bindings/target/$(RUSTC_TARGET_NAME)/release

# 1. cargo (offline, vendored) builds the staticlib; build.rs writes the
#    cbindgen header where the plugin looks for it.
# 2. CMake builds the plugin against it (0002 patch: no Corrosion fetch).
define CUTECOSMIC_BUILD_CMDS
	cd $(@D)/bindings && \
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
		BINDINGS_HEADER_PATH=$(@D)/bindings/bindings.h \
		cargo build --offline --release --locked --manifest-path Cargo.toml
	$(TARGET_MAKE_ENV) $(BR2_CMAKE) -S $(@D) -B $(@D)/build \
		-DCMAKE_TOOLCHAIN_FILE="$(HOST_DIR)/share/buildroot/toolchainfile.cmake" \
		-DCMAKE_BUILD_TYPE=Release \
		-DQT_HOST_PATH=$(HOST_DIR) \
		-DCUTECOSMIC_BINDINGS_LIB=$(CUTECOSMIC_BINDINGS_OUT)/libbindings.a \
		-DCUTECOSMIC_BINDINGS_INCLUDE_DIR=$(@D)/bindings \
		-DCUTECOSMIC_BINDINGS_LINK_LIBS="dl;pthread;m;rt;util;gcc_s"
	$(TARGET_MAKE_ENV) $(BR2_CMAKE) --build $(@D)/build -j$(PARALLEL_JOBS)
endef

define CUTECOSMIC_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/build/platformtheme/libcutecosmictheme.so \
		$(TARGET_DIR)/usr/lib/qt6/plugins/platformthemes/libcutecosmictheme.so
endef

$(eval $(cargo-package))
