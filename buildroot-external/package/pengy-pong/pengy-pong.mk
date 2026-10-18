################################################################################
#
# pengy-pong
#
################################################################################

# Buildroot recipe for the PengyOS pack-in game Penguin Pong
# (tools/pengy-pong in this repo).
#
# The source is THIS REPO, not a tarball: the crate lives in tools/pengy-pong
# and Buildroot copies it in with the `local` site method. That is why there is
# no download URL and no `.hash` file — the two rules a digest serves (verify a
# fetch, pin a release) do not apply to a source tree the build is already
# running from, and the version pin below is what ties the built binary to the
# crate's own semver (see tools/pengy-pong/Cargo.toml).
#
# The crate has ZERO external dependencies (enforced by the empty
# [dependencies] table and by tests/github_ready.rs), so this recipe needs no
# vendored crates, no crate digests and no C to cross-compile: the build is a
# plain offline `cargo build --release`. That is the whole reason the pack-in
# games are written std-only.

PENGY_PONG_VERSION = 0.1.0
PENGY_PONG_SITE = $(BR2_EXTERNAL_PENGYOS_PATH)/../../tools/pengy-pong
PENGY_PONG_SITE_METHOD = local

PENGY_PONG_INSTALL_TARGET = YES

# tools/pengy-pong/LICENSE is the MIT license (Copyright (c) 2026 Pat Wendorf).
PENGY_PONG_LICENSE = MIT
PENGY_PONG_LICENSE_FILES = LICENSE

# The cargo-package macro already adds host-rustc; the game needs nothing else
# (std only — no systemd, no libraries, no host tools).
PENGY_PONG_DEPENDENCIES =

# The crate's own source directory, as copied in by the `local` site method.
PENGY_PONG_SRCDIR = $(@D)

# Build the game binary from the copied source tree. No `--locked`: the crate
# has no dependencies, so there is no lockfile to carry, and `--offline` proves
# the build never reaches the network.
define PENGY_PONG_BUILD_CMDS
	cd $(PENGY_PONG_SRCDIR) && \
		$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
			cargo build --offline --release \
				--manifest-path Cargo.toml --bin pengy-pong
endef

# Install the binary to /usr/bin and the menu entry. The .desktop file is
# installed from the source tree (tools/pengy-pong/launcher/) so the launcher
# and the game can never drift apart.
#
# At run time the game writes only
# $XDG_DATA_HOME/pengyos/games/pengy-pong/ (default ~/.local/share/...),
# under $HOME; the recipe installs nothing the game writes to.
define PENGY_PONG_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(PENGY_PONG_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/pengy-pong \
		$(TARGET_DIR)/usr/bin/pengy-pong
	$(INSTALL) -D -m 0644 $(PENGY_PONG_SRCDIR)/launcher/pengy-pong.desktop \
		$(TARGET_DIR)/usr/share/applications/pengy-pong.desktop
endef

$(eval $(cargo-package))
