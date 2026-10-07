################################################################################
#
# uutils-coreutils
#
################################################################################

# Buildroot recipe for the uutils/coreutils multicall binary.
#
# uutils/coreutils ships a single multicall 'coreutils' binary that dispatches
# on argv[0]: running the binary under any utility name runs that utility. We
# therefore build once and expose every utility through a symlink -> coreutils
# (busybox-style multi-call dispatch).
#
# The stock 'cargo-package' macro only installs the root package's binaries
# (cargo install --bins --path ./), which for uutils is just the multicall
# 'coreutils' binary, not the individual utility crates in src/uu/*. We build
# the multicall binary ourselves and create the symlinks by hand.

UUTILS_COREUTILS_VERSION = 0.11.0
UUTILS_COREUTILS_SOURCE = coreutils-$(UUTILS_COREUTILS_VERSION).tar.gz
UUTILS_COREUTILS_SITE = $(call github,uutils,coreutils,$(UUTILS_COREUTILS_VERSION))
UUTILS_COREUTILS_INSTALL_TARGET = YES

# Rust: build the multicall 'coreutils' binary, then expose every utility
# through a symlink to it (busybox-style multi-call dispatch on argv[0]).
UUTILS_COREUTILS_LICENSE = MIT
UUTILS_COREUTILS_LICENSE_FILES = LICENSE

# Utilities exposed via a symlink -> coreutils. This is the set of utilities
# the multicall binary ships by default (Cargo.toml feat_common_core for the
# pinned version). Regenerate this list when bumping UUTILS_COREUTILS_VERSION.
UUTILS_COREUTILS_UTILITIES = \
	base32 base64 basename basenc cat cksum b2sum md5sum sha1sum \
	sha224sum sha256sum sha384sum sha512sum comm cp csplit cut date dd \
	df dir dircolors dirname du echo env expand expr factor false fmt fold \
	head join link ln ls mkdir mktemp more mv nl numfmt od paste pathchk \
	pr printenv printf ptx pwd readlink realpath rm rmdir seq shred shuf \
	sleep sort split sum tac tail tee test touch tr true truncate tsort \
	tty unexpand uniq unlink vdir wc yes

# Build the multicall binary for the target using Buildroot's cargo
# infrastructure (vendored deps, locked, target triple via CARGO_BUILD_TARGET).
define UUTILS_COREUTILS_BUILD_CMDS
	cd $(UUTILS_COREUTILS_SRCDIR) && \
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
		cargo build --offline --release --locked --manifest-path Cargo.toml \
			--bin coreutils
endef

# Install the multicall binary and symlink every utility to it.
define UUTILS_COREUTILS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(UUTILS_COREUTILS_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/coreutils \
		$(TARGET_DIR)/usr/bin/coreutils
	$(foreach util,$(UUTILS_COREUTILS_UTILITIES), \
		ln -sf coreutils $(TARGET_DIR)/usr/bin/$(util); )
endef

$(eval $(cargo-package))
