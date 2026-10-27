################################################################################
#
# uutils-findutils
#
################################################################################

# PengyOS's find and xargs: uutils/findutils, the Rust reimplementation of GNU
# findutils, for the uutils-coreutils reasons: MIT (GNU findutils is GPL-3.0),
# and memory safety in the tool every script and agent runs over untrusted
# filenames. Buildroot 2026.08 has no recipe, so this one is ours.
#
# Adopted on evidence, not on the test-suite number. uutils/findutils passed 88%
# of GNU's suite and 87% of BFS's (uutils/findutils-tracking, 2026-10-26). What
# decided it: every find/xargs invocation in the image's scripts (distrobox,
# lvm's fsadm, rescan-scsi-bus, usb-devices, the xdg-utils), plus the everyday
# options (-printf, -print0 | xargs -0, -prune, -wholename/-iname with -o, -L,
# -exec {} +, -regextype, -newer, -size, -mtime, -empty, -perm, xargs -d -I -P
# -r -n, the exit codes) gave byte-identical output to GNU findutils 4.10.0 in a
# side-by-side run on 2026-10-27. GNU diffutils stays: uutils/diffutils 0.6.0
# has no recursive diff (`diff -r`), no diff3 or sdiff.
#
# Upstream builds four programs and a test helper; we build the four. They link
# Buildroot's oniguruma (the onig crate's RUSTONIG_SYSTEM_LIBONIG path) rather
# than compiling the copy onig_sys bundles, so the regex engine is one library
# pkg-stats and the CVE scan can see.
#
# Vendoring is Buildroot's cargo infrastructure: `cargo vendor` over the pinned
# Cargo.lock at download time, `--offline --locked` at build time.
#
# Observed by pengyos-nightly's `uutils-findutils` source (git tags): there is
# no release-monitoring.org project for it, so pkg-stats cannot see it age.

UUTILS_FINDUTILS_VERSION = 0.10.0
UUTILS_FINDUTILS_SITE = $(call github,uutils,findutils,$(UUTILS_FINDUTILS_VERSION))
UUTILS_FINDUTILS_LICENSE = MIT
UUTILS_FINDUTILS_LICENSE_FILES = LICENSE
UUTILS_FINDUTILS_DEPENDENCIES = host-pkgconf oniguruma
UUTILS_FINDUTILS_INSTALL_TARGET = YES

UUTILS_FINDUTILS_PROGRAMS = find xargs locate updatedb

define UUTILS_FINDUTILS_BUILD_CMDS
	cd $(UUTILS_FINDUTILS_SRCDIR) && \
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
		RUSTONIG_SYSTEM_LIBONIG=1 PKG_CONFIG_ALLOW_CROSS=1 \
		cargo build --offline --release --locked \
			--manifest-path Cargo.toml \
			$(foreach p,$(UUTILS_FINDUTILS_PROGRAMS),--bin $(p))
endef

define UUTILS_FINDUTILS_INSTALL_TARGET_CMDS
	$(foreach p,$(UUTILS_FINDUTILS_PROGRAMS), \
		$(INSTALL) -D -m 0755 \
			$(UUTILS_FINDUTILS_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/$(p) \
			$(TARGET_DIR)/usr/bin/$(p);$(sep))
endef

$(eval $(cargo-package))
