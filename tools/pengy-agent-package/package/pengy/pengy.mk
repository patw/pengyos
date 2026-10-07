################################################################################
#
# pengy
#
################################################################################

# Buildroot external package for the PengyR agent (https://github.com/patw/PengyR).
#
# PengyR ships three front-ends from one agent core: a Qt6 desktop GUI (`pengy`),
# a headless command-line interface (`pengy-cli`) and a headless web server
# (`pengy-web`). PengyOS Core is headless (see spec.md Non-goals), so this recipe
# builds ONLY the two Rust targets that have no Qt/GUI dependency and installs
# just those two binaries.
#
# The stock `cargo-package` macro runs `cargo install --path ./`, which builds
# the *root* crate of the workspace. For PengyR the root crate is `pengy_core`,
# a library with no binary, so `--bins` would install nothing. We therefore build
# the two binaries explicitly and copy them out of the cargo target directory
# ourselves (see PENGY_BUILD_CMDS / PENGY_INSTALL_TARGET_CMDS below).

PENGY_VERSION = 1.9.0
PENGY_SITE = https://github.com/patw/PengyR/archive/v$(PENGY_VERSION)
# NOTE: SOURCE is a literal filename (not "$(PENGY_VERSION)") on purpose. The
# Buildroot download URL is SITE + "/" + SOURCE, so Buildroot still expands the
# version in SITE to build https://github.com/patw/PengyR/archive/v1.9.0/
# PengyR-1.9.0.tar.gz. SOURCE stays literal so the .hash filename matches it and
# `pengyos-recipe-lint`'s hash_present rule can verify the digest.
PENGY_SOURCE = PengyR-1.9.0.tar.gz

PENGY_INSTALL_TARGET = YES

# PengyR is MIT — the LICENSE at the root of the pinned tarball is the MIT
# license (Copyright (c) 2025 Pat Wendorf).
PENGY_LICENSE = MIT
PENGY_LICENSE_FILES = LICENSE

# The recipe ships a systemd unit for the headless web agent, so systemd is a
# build dependency. The cargo-package macro already adds host-rustc, so this is
# the only explicit dependency.
PENGY_DEPENDENCIES = systemd

# The PengyR workspace has three members (`.`, `cli`, `web`). The root crate is a
# library; the only two *binaries* are `pengy-cli` and `pengy-web`. The Qt GUI is
# a separate CMake target (`gui/`) that is NOT a workspace member, so `cargo build`
# never touches it. We build the two headless binaries explicitly and install them
# to the target userland bin.
define PENGY_BUILD_CMDS
	cd $(PENGY_SRCDIR) && \
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
		cargo build --offline --release --locked --manifest-path Cargo.toml \
			--bin pengy-cli --bin pengy-web
endef

# Install the two headless binaries. The stock cargo-package install
# (`cargo install --path ./`) would target the root library crate and install
# nothing, so copy the built artifacts from the cargo target directory.
define PENGY_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(PENGY_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/pengy-cli \
		$(TARGET_DIR)/usr/bin/pengy-cli
	$(INSTALL) -D -m 0755 \
		$(PENGY_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/pengy-web \
		$(TARGET_DIR)/usr/bin/pengy-web
	$(INSTALL) -D -m 0644 $(PENGY_PKGDIR)/settings.json \
		$(TARGET_DIR)/etc/pengy/settings.json
endef

# Install the hardened systemd unit for the headless web agent. Buildroot creates
# the enablement symlink automatically when systemd is the init system.
define PENGY_INSTALL_INIT_SYSTEMD
	$(INSTALL) -D -m 0644 $(PENGY_PKGDIR)/pengy-web.service \
		$(TARGET_DIR)/usr/lib/systemd/system/pengy-web.service
endef

$(eval $(cargo-package))
