################################################################################
#
# zram-generator
#
################################################################################

ZRAM_GENERATOR_VERSION = 1.2.1
# The crates.io release, not GitHub's tag tarball: only the crate carries a
# Cargo.lock, which Buildroot's vendoring (cargo vendor --locked) needs.
ZRAM_GENERATOR_SOURCE = zram-generator-$(ZRAM_GENERATOR_VERSION).crate
ZRAM_GENERATOR_SITE = https://static.crates.io/crates/zram-generator
ZRAM_GENERATOR_LICENSE = MIT
ZRAM_GENERATOR_LICENSE_FILES = LICENSE
ZRAM_GENERATOR_DEPENDENCIES = systemd
# Compiled in: where systemd-makefs lives on the target.
ZRAM_GENERATOR_CARGO_ENV = SYSTEMD_UTIL_DIR=/usr/lib/systemd

# cargo install puts the binary in /usr/bin; systemd runs it from the generator
# directory, and the setup unit calls it there (upstream's Makefile install).
define ZRAM_GENERATOR_INSTALL_SYSTEMD_PARTS
	mkdir -p $(TARGET_DIR)/usr/lib/systemd/system-generators
	mv $(TARGET_DIR)/usr/bin/zram-generator \
		$(TARGET_DIR)/usr/lib/systemd/system-generators/zram-generator
	sed -e 's,@SYSTEMD_SYSTEM_GENERATOR_DIR@,/usr/lib/systemd/system-generators,' \
		$(@D)/units/systemd-zram-setup@.service.in \
		> $(TARGET_DIR)/usr/lib/systemd/system/systemd-zram-setup@.service
	$(INSTALL) -D -m 0644 $(ZRAM_GENERATOR_PKGDIR)/zram-generator.conf \
		$(TARGET_DIR)/usr/lib/systemd/zram-generator.conf
	$(INSTALL) -D -m 0644 $(ZRAM_GENERATOR_PKGDIR)/99-zram.conf \
		$(TARGET_DIR)/usr/lib/sysctl.d/99-zram.conf
endef
ZRAM_GENERATOR_POST_INSTALL_TARGET_HOOKS += ZRAM_GENERATOR_INSTALL_SYSTEMD_PARTS

$(eval $(cargo-package))
