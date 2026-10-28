################################################################################
#
# uutils-coreutils
#
################################################################################

# PengyOS's core utilities: uutils/coreutils, the Rust reimplementation of GNU
# coreutils (SPEC §2.1). Chosen over GNU coreutils for three reasons: MIT (the
# rest of the base is MIT/Apache; GPL-3 coreutils was a documented exception),
# memory safety in the code that parses untrusted filenames and arguments, and
# the Ubuntu precedent (25.10 ships uutils as the default coreutils).
#
# Upstream ships ONE multicall binary, `coreutils`, that dispatches on argv[0]
# (busybox-style). We build it once and symlink every utility to it, so the
# image has `ls`, `cp`, `stat`, … as ordinary /usr/bin entries.
#
# Build with `--no-default-features --features unix` (= upstream's feat_os_unix):
# the 78 feat_common_core utilities PLUS the unix-only ones GNU coreutils ships
# on Linux - arch, chgrp, chmod, chown, chroot, groups, hostname, id, install,
# kill, logname, mkfifo, mknod, nice, nohup, nproc, pinky, stat, stdbuf, stty,
# sync, timeout, uname, uptime, users, who, whoami, hostid. The default feature
# set does NOT include those; building the default would silently drop `stat`,
# `id`, `sync`, `uname`, `whoami`, `install`, `stty` and `timeout` from the
# image, all of which Core ships today.
#
# Deliberately OFF: `openssl` (vendored libcrypto for the hashsum utilities -
# the pure-Rust implementations are fast enough and it keeps the OpenSSL surface
# to one library), `selinux`/`feat_selinux` (needs libselinux + an SELinux
# kernel; chcon/runcon are not shipped), `feat_systemd_logind` (utmpx is real
# here), `feat_diagnostics` (default-on caret rendering pulls ariadne into every
# utility; `--no-default-features` drops it).
#
# Vendoring is Buildroot's cargo infrastructure: the download post-process runs
# `cargo vendor` over the pinned Cargo.lock, so the build is `--offline` and
# `--locked` - no network at build time, no floating dependency versions.
#
# BUILD/INSTALL are written out rather than left to cargo-package's defaults:
# the default `cargo install --bins` path is right for a single-binary crate,
# and we want the multicall binary placed by $(INSTALL) plus a symlink per
# utility - the same shape every other PengyOS multicall install uses.

UUTILS_COREUTILS_VERSION = 0.11.0
UUTILS_COREUTILS_SOURCE = coreutils-$(UUTILS_COREUTILS_VERSION).tar.gz
UUTILS_COREUTILS_SITE = $(call github,uutils,coreutils,$(UUTILS_COREUTILS_VERSION))
UUTILS_COREUTILS_LICENSE = MIT
UUTILS_COREUTILS_LICENSE_FILES = LICENSE
UUTILS_COREUTILS_INSTALL_TARGET = YES

# Utilities exposed by a symlink -> coreutils. This is upstream's feat_os_unix
# set for the pinned version (106 names); regenerate it from the pinned
# Cargo.toml when bumping UUTILS_COREUTILS_VERSION. The authoritative fixture is
# scripts/tests/fixtures/coreutils-0.12.0-unix-utilities.txt, asserted by
# scripts/tests/test_uutils_recipe.py.
UUTILS_COREUTILS_UTILITIES = \
	arch b2sum base32 base64 basename basenc cat chgrp chmod chown chroot cksum \
	comm cp csplit cut date dd df dir dircolors dirname du echo env expand expr \
	factor false fmt fold groups head hostid hostname id install join kill link \
	ln logname ls md5sum mkdir mkfifo mknod mktemp more mv nice nl nohup nproc \
	numfmt od paste pathchk pinky pr printenv printf ptx pwd readlink realpath \
	rm rmdir seq sha1sum sha224sum sha256sum sha384sum sha512sum shred shuf \
	sleep sort split stat stdbuf stty sum sync tac tail tee test timeout touch \
	tr true truncate tsort tty uname unexpand uniq unlink uptime users vdir wc \
	who whoami yes

# Build the multicall binary for the target through Buildroot's cargo
# infrastructure: vendored deps (--offline), locked versions (--locked),
# target triple from CARGO_BUILD_TARGET in $(PKG_CARGO_ENV).
define UUTILS_COREUTILS_BUILD_CMDS
	cd $(UUTILS_COREUTILS_SRCDIR) && \
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
		cargo build --offline --release --locked \
			--no-default-features --features unix \
			--manifest-path Cargo.toml \
			--bin coreutils
endef

# Install the multicall binary, then expose every utility as a symlink to it
# (busybox-style multi-call dispatch on argv[0]).
define UUTILS_COREUTILS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(UUTILS_COREUTILS_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/coreutils \
		$(TARGET_DIR)/usr/bin/coreutils
	$(foreach util,$(UUTILS_COREUTILS_UTILITIES), \
		ln -sf coreutils $(TARGET_DIR)/usr/bin/$(util);$(sep))
endef

$(eval $(cargo-package))
