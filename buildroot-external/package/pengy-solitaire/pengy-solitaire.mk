################################################################################
#
# pengy-solitaire
#
################################################################################

# Buildroot recipe for the PengyOS pack-in game Frostbite Solitaire
# (tools/pengy-solitaire in this repo).
#
# The source is THIS REPO, not a tarball: the crate lives in tools/pengy-solitaire
# and Buildroot copies it in with the `local` site method. That is why there is
# no download URL and no `.hash` file — the two rules a digest serves (verify a
# fetch, pin a release) do not apply to a source tree the build is already
# running from, and the version pin below is what ties the built binary to the
# crate's own semver (see tools/pengy-solitaire/Cargo.toml).
#
# The crate has ZERO external dependencies (enforced by the empty
# [dependencies] table and by tests/github_ready.rs), so this recipe needs no
# vendored crates, no crate digests and no C to cross-compile: the build is a
# plain offline `cargo build --release`. That is the whole reason the pack-in
# games are written std-only.

PENGY_SOLITAIRE_VERSION = 0.1.0
PENGY_SOLITAIRE_SITE = $(BR2_EXTERNAL_PENGYOS_PATH)/../../tools/pengy-solitaire
PENGY_SOLITAIRE_SITE_METHOD = local

PENGY_SOLITAIRE_INSTALL_TARGET = YES

# tools/pengy-solitaire/LICENSE is the MIT license (Copyright (c) 2026 Pat Wendorf).
PENGY_SOLITAIRE_LICENSE = MIT
PENGY_SOLITAIRE_LICENSE_FILES = LICENSE

# The cargo-package macro already adds host-rustc; the game needs nothing else
# (std only — no systemd, no libraries, no host tools). It reads the keyboard
# through `stty`, which every BusyBox provides, and it runs on a plain terminal:
# no GPU, no compositor, no X, no Wayland.
PENGY_SOLITAIRE_DEPENDENCIES =

# The crate's own source directory, as copied in by the `local` site method.
PENGY_SOLITAIRE_SRCDIR = $(@D)

# Build the game binary from the copied source tree. No `--locked`: the crate
# has no dependencies, so there is no lockfile to carry, and `--offline` proves
# the build never reaches the network.
define PENGY_SOLITAIRE_BUILD_CMDS
	cd $(PENGY_SOLITAIRE_SRCDIR) && \
		$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
			cargo build --offline --release \
				--manifest-path Cargo.toml --bin pengy-solitaire
endef

# Install the binary to /usr/bin and the menu entry. The .desktop file is
# installed from the source tree (tools/pengy-solitaire/launcher/) so the
# launcher and the game can never drift apart.
#
# At run time the game writes only
# $XDG_DATA_HOME/pengyos/games/pengy-solitaire/ (default ~/.local/share/...),
# under the player's home; the recipe installs nothing the game writes to.
define PENGY_SOLITAIRE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(PENGY_SOLITAIRE_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/pengy-solitaire \
		$(TARGET_DIR)/usr/bin/pengy-solitaire
	$(INSTALL) -D -m 0644 $(PENGY_SOLITAIRE_SRCDIR)/launcher/pengy-solitaire.desktop \
		$(TARGET_DIR)/usr/share/applications/pengy-solitaire.desktop
endef

$(eval $(cargo-package))
