################################################################################
#
# sudo-rs
#
################################################################################

# PengyOS's sudo: sudo-rs, the Rust reimplementation of sudo by the Trifecta
# Tech Foundation, replacing Buildroot's C sudo (1.9.17p2 up to the switch).
# The reasons are the uutils-coreutils ones: memory safety in a setuid-root
# binary that parses untrusted arguments and environment, a smaller feature
# surface (no plugins, no LDAP, no sudo_logsrvd/sudoreplay), and the Ubuntu
# precedent (25.10 and 26.04 ship sudo-rs as the default sudo). Buildroot
# 2026.08 has no sudo-rs recipe, so this one is ours.
#
# Pinned to the newest upstream release, not Ubuntu 26.04's 0.2.13: 0.2.15
# fixes a sudoedit symlink race (GHSA-f42v-x7gq-phc8) and dynamically loaded
# groups being ignored in policy decisions.
#
# Built with the default (empty) feature set:
#   - no `pam-login`: `sudo -i` uses the `sudo` PAM service, which is the only
#     file we install (and /etc/pam.d/other catches anything else);
#   - no `apparmor`: the kernel has no AppArmor;
#   - no `gettext`: English messages, like the rest of the base system.
# `su` is not built: Core ships util-linux's su, and sudo lives in Server.
#
# Vendoring is Buildroot's cargo infrastructure, as for uutils-coreutils: the
# download post-process runs `cargo vendor` over the pinned Cargo.lock, so the
# build is `--offline --locked`. sudo-rs has two dependencies (libc, glob).

SUDO_RS_VERSION = 0.2.15
SUDO_RS_SITE = $(call github,trifectatechfoundation,sudo-rs,v$(SUDO_RS_VERSION))
SUDO_RS_LICENSE = Apache-2.0 or MIT
SUDO_RS_LICENSE_FILES = LICENSE-APACHE LICENSE-MIT COPYRIGHT
# NVD files sudo-rs CVEs (CVE-2025-46717, -46718) as trifectatech:sudo with
# target_sw=rust. The default (sudo-rs_project:sudo-rs) would match none of them.
SUDO_RS_CPE_ID_VENDOR = trifectatech
SUDO_RS_CPE_ID_PRODUCT = sudo
SUDO_RS_DEPENDENCIES = linux-pam
SUDO_RS_INSTALL_TARGET = YES

define SUDO_RS_BUILD_CMDS
	cd $(SUDO_RS_SRCDIR) && \
	$(TARGET_MAKE_ENV) $(TARGET_CONFIGURE_OPTS) $(PKG_CARGO_ENV) \
		cargo build --offline --release --locked \
			--manifest-path Cargo.toml \
			--bin sudo --bin visudo
endef

# The binaries, the sudoers file and the PAM service. No `sudo` group: the
# admins are wheel (see the sudoers file). /etc/sudoers.d is the
# Server overlay's (sudoers.d/wheel); post-build makes its files 0440.
# The PAM file is a copy of Buildroot's package/sudo/sudo.pam, the one the C
# sudo installed, so the PAM stack (pam_wheel use_uid included) does not change.
define SUDO_RS_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(SUDO_RS_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/sudo \
		$(TARGET_DIR)/usr/bin/sudo
	ln -sf sudo $(TARGET_DIR)/usr/bin/sudoedit
	$(INSTALL) -D -m 0755 \
		$(SUDO_RS_SRCDIR)/target/$(RUSTC_TARGET_NAME)/release/visudo \
		$(TARGET_DIR)/usr/bin/visudo
	$(INSTALL) -D -m 0440 $(SUDO_RS_PKGDIR)/sudoers $(TARGET_DIR)/etc/sudoers
	$(INSTALL) -D -m 0644 $(SUDO_RS_PKGDIR)/sudo.pam $(TARGET_DIR)/etc/pam.d/sudo
	mkdir -p $(TARGET_DIR)/etc/sudoers.d
endef

define SUDO_RS_PERMISSIONS
	/usr/bin/sudo f 4755 0 0 - - - - -
	/etc/sudoers f 0440 0 0 - - - - -
endef

$(eval $(cargo-package))
