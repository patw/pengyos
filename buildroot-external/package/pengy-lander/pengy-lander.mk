################################################################################
#
# pengy-lander
#
################################################################################

# Buildroot recipe for the PengyOS pack-in game Ice Lander
# (tools/pengy-lander in this repo).
#
# The source is THIS REPO, not a tarball: the crate lives in tools/pengy-lander
# and Buildroot copies it in with the `local` site method. That is why there is
# no download URL and no `.hash` file — the two rules a digest serves (verify a
# fetch, pin a release) do not apply to a source tree the build is already
# running from, and the version pin below is what ties the built binary to the
# crate's own semver (see tools/pengy-lander/Cargo.toml).
#
# The crate has ZERO external dependencies (enforced by the empty
# [dependencies] table and by tests/github_ready.rs), so this recipe needs no
# vendored crates, no crate digests and no C to cross-compile: the build is a
# plain offline `cargo build --release`. That is the whole reason the pack-in
# games are written std-only.

PENGY_LANDER_VERSION = 0.1.0
PENGY_LANDER_SITE = $(BR2_EXTERNAL_PENGYOS_PATH)/../../tools/pengy-lander
PENGY_LANDER_SITE_METHOD = local

PENGY_LANDER_INSTALL_TARGET = YES

# tools/pengy-lander/LICENSE is the MIT license (Copyright (c) 2026 Pat Wendorf).
PENGY_LANDER_LICENSE = MIT
PENGY_LANDER_LICENSE_FILES = LICENSE

# The cargo-package macro already adds host-rustc; the game needs nothing else
# (std only — no systemd, no libraries, no host tools).
PENGY_LANDER_DEPENDENCIES =

# The crate's own source directory, as copied in by the `local` site method.
# Buildroot defines $(<pkg>_SRCDIR); a $(PKG_..._SRCDIR) is never defined and
# `cd ` would run in the build host's HOME.
PENGY_LANDER_SRCDIR = $(@D)

# Build the game binary from the copied source tree. No `--locked`: the crate
# has no dependencies, so there is no lockfile to carry, and `--offline` proves
# the build never reaches the network.
define PENGY_LANDER_BUILD_CMDS
	cd $(PENGY_LANDER_SRCDIR) && \
		$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
			cargo build --offline --release \
				--manifest-path Cargo.toml --bin pengy-lander
endef

# Install the binary to /usr/bin and the menu entry. The .desktop file is
# installed from the source tree (tools/pengy-lander/launcher/) so the launcher
# and the game can never drift apart.
#
# At run time the game writes only
# $XDG_DATA_HOME/pengyos/games/pengy-lander/ (default ~/.local/share/...),
# under $HOME; the recipe installs nothing the game writes to.
define PENGY_LANDER_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(PENGY_LANDER_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/pengy-lander \
		$(TARGET_DIR)/usr/bin/pengy-lander
	$(INSTALL) -D -m 0644 $(PENGY_LANDER_SRCDIR)/launcher/pengy-lander.desktop \
		$(TARGET_DIR)/usr/share/applications/pengy-lander.desktop
endef

$(eval $(cargo-package))
