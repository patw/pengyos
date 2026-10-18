################################################################################
#
# pengy-floe
#
################################################################################

# Buildroot recipe for the PengyOS pack-in game Flappy Pengy
# (tools/pengy-floe in this repo).
#
# The source is THIS REPO, not a tarball: the crate lives in tools/pengy-floe
# and Buildroot copies it in with the `local` site method. That is why there is
# no download URL and no `.hash` file — the two rules that a digest serves
# (verify a fetch, pin a release) do not apply to a source tree the build is
# already running from, and the version pin below is what ties the built binary
# to the crate's own semver (see tools/pengy-floe/Cargo.toml).
#
# The crate has ZERO external dependencies (enforced by the empty
# [dependencies] table and by tests/github_ready.rs), so this recipe needs no
# vendored crates, no crate digests and no C to cross-compile: the build is a
# plain offline `cargo build --release`. That is the whole reason the pack-in
# games are written std-only.

PENGY_FLOE_VERSION = 0.1.0
PENGY_FLOE_SITE = $(BR2_EXTERNAL_PENGYOS_PATH)/../../tools/pengy-floe
PENGY_FLOE_SITE_METHOD = local

PENGY_FLOE_INSTALL_TARGET = YES

# tools/pengy-floe/LICENSE is the MIT license (Copyright (c) 2026 Pat Wendorf).
PENGY_FLOE_LICENSE = MIT
PENGY_FLOE_LICENSE_FILES = LICENSE

# The cargo-package macro already adds host-rustc; the game needs nothing else
# (std only — no systemd, no libraries, no host tools).
PENGY_FLOE_DEPENDENCIES =

# Build the game binary from the copied source tree. No `--locked`: the crate
# has no dependencies, so there is no lockfile to carry, and `--offline` proves
# the build never reaches the network.
define PENGY_FLOE_BUILD_CMDS
	cd $(PKG_FLOE_SRCDIR) && \
		$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
			cargo build --offline --release \
				--manifest-path Cargo.toml --bin pengy-floe
endef

# Install the binary to /usr/bin and the menu entry. The .desktop file is
# installed from the source tree (tools/pengy-floe/launcher/) so the game's
# launcher and the game itself can never drift apart.
#
# The game writes only $XDG_DATA_HOME/pengyos/games/pengy-floe/ (under $HOME)
# at run time; the recipe installs nothing that the game writes to.
define PENGY_FLOE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(PKG_FLOE_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/pengy-floe \
		$(TARGET_DIR)/usr/bin/pengy-floe
	$(INSTALL) -D -m 0644 $(PKG_FLOE_SRCDIR)/launcher/pengy-floe.desktop \
		$(TARGET_DIR)/usr/share/applications/pengy-floe.desktop
endef

$(eval $(cargo-package))
