################################################################################
#
# pengy-blocks
#
################################################################################

# Buildroot recipe for the PengyOS pack-in game Glacier Blocks
# (tools/pengy-blocks in this repo).
#
# The source is THIS REPO, not a tarball: the crate lives in tools/pengy-blocks
# and Buildroot copies it in with the `local` site method. That is why there is
# no download URL and no `.hash` file — the two rules a digest serves (verify a
# fetch, pin a release) do not apply to a source tree the build is already
# running from, and the version pin below is what ties the built binary to the
# crate's own semver (see tools/pengy-blocks/Cargo.toml).
#
# The crate has ZERO external dependencies (enforced by the empty
# [dependencies] table and by tests/github_ready.rs), so this recipe needs no
# vendored crates, no crate digests and no C to cross-compile: the build is a
# plain offline `cargo build --release`. That is the whole reason the pack-in
# games are written std-only.

PENGY_BLOCKS_VERSION = 0.1.0
PENGY_BLOCKS_SITE = $(BR2_EXTERNAL_PENGYOS_PATH)/../../tools/pengy-blocks
PENGY_BLOCKS_SITE_METHOD = local

PENGY_BLOCKS_INSTALL_TARGET = YES

# tools/pengy-blocks/LICENSE is the MIT license (Copyright (c) 2026 Pat Wendorf).
PENGY_BLOCKS_LICENSE = MIT
PENGY_BLOCKS_LICENSE_FILES = LICENSE

# The cargo-package macro already adds host-rustc; the game needs nothing else
# (std only — no systemd, no libraries, no host tools).
PENGY_BLOCKS_DEPENDENCIES =

# The crate's own source directory, as copied in by the `local` site method.
PENGY_BLOCKS_SRCDIR = $(@D)

# Build the game binary from the copied source tree. No `--locked`: the crate
# has no dependencies, so there is no lockfile to carry, and `--offline` proves
# the build never reaches the network.
define PENGY_BLOCKS_BUILD_CMDS
	cd $(PENGY_BLOCKS_SRCDIR) && \
		$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
			cargo build --offline --release \
				--manifest-path Cargo.toml --bin pengy-blocks
endef

# Install the binary to /usr/bin and the menu entry. The .desktop file is
# installed from the source tree (tools/pengy-blocks/launcher/) so the launcher
# and the game can never drift apart.
#
# At run time the game writes only
# $XDG_DATA_HOME/pengyos/games/pengy-blocks/ (default ~/.local/share/...),
# under the player's home; the recipe installs nothing the game writes to.
define PENGY_BLOCKS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(PENGY_BLOCKS_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/pengy-blocks \
		$(TARGET_DIR)/usr/bin/pengy-blocks
	$(INSTALL) -D -m 0644 $(PENGY_BLOCKS_SRCDIR)/launcher/pengy-blocks.desktop \
		$(TARGET_DIR)/usr/share/applications/pengy-blocks.desktop
endef

$(eval $(cargo-package))
