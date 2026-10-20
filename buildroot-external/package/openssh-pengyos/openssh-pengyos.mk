################################################################################
#
# openssh-pengyos - PengyOS's fork of the stock openssh package.
#
# Why this file exists: divergence.toml -> openssh-10.6p1-fork. Buildroot
# 2026.08 pins OpenSSH 10.5p1, which carries remote unauthenticated sshd
# vulnerabilities (CVE-2026-106585, CVE-2026-106582) that upstream fixed in
# 10.6, and a br2-external tree cannot move a stock package's version - proven
# in tools/pengyos-nightly/pengyos_nightly/edits.py. NVD names no fix commit for
# any of the five, so there is nothing to carry as a patch: the fix is the
# version. This recipe is a faithful copy of package/openssh/openssh.mk from
# Buildroot 2026.08 with the version moved and the prefix renamed; read it
# against that file. It is deleted, with the defconfig lines, by the expiry in
# the ledger entry.
#
################################################################################

OPENSSH_PENGYOS_VERSION_MAJOR = 10.6
OPENSSH_PENGYOS_VERSION_MINOR = p1
OPENSSH_PENGYOS_VERSION = $(OPENSSH_PENGYOS_VERSION_MAJOR)$(OPENSSH_PENGYOS_VERSION_MINOR)
OPENSSH_PENGYOS_CPE_ID_VENDOR = openbsd
OPENSSH_PENGYOS_CPE_ID_VERSION = $(OPENSSH_PENGYOS_VERSION_MAJOR)
OPENSSH_PENGYOS_CPE_ID_UPDATE = $(OPENSSH_PENGYOS_VERSION_MINOR)
OPENSSH_PENGYOS_SITE = https://ftp.openbsd.org/pub/OpenBSD/OpenSSH/portable
# Buildroot's default SOURCE is <pkg>-<version>.tar.gz, i.e. openssh-pengyos-10.6p1.tar.gz,
# which upstream does not publish: the tarball is named openssh-10.6p1.tar.gz. Naming it
# here is what makes the download resolve; the .hash line is keyed to this same name.
OPENSSH_PENGYOS_SOURCE = openssh-$(OPENSSH_PENGYOS_VERSION).tar.gz
OPENSSH_PENGYOS_LICENSE = BSD-3-Clause, BSD-2-Clause, Public Domain
OPENSSH_PENGYOS_LICENSE_FILES = LICENCE

OPENSSH_PENGYOS_CONF_ENV = \
	LD="$(TARGET_CC)" \
	LDFLAGS="$(TARGET_CFLAGS)" \
	LIBS=`$(PKG_CONFIG_HOST_BINARY) --libs openssl`
OPENSSH_PENGYOS_CONF_OPTS = \
	--sysconfdir=/etc/ssh \
	--with-pid-dir=/var/run \
	--with-default-path=$(BR2_SYSTEM_DEFAULT_PATH) \
	$(if $(BR2_PACKAGE_OPENSSH_PENGYOS_SANDBOX),--with-sandbox,--without-sandbox) \
	--disable-lastlog \
	--disable-utmp \
	--disable-utmpx \
	--disable-wtmp \
	--disable-wtmpx \
	--disable-strip

OPENSSH_PENGYOS_SELINUX_MODULES = ssh

define OPENSSH_PENGYOS_PERMISSIONS
	/var/empty d 755 root root - - - - -
endef

ifeq ($(BR2_powerpc64le),y)
OPENSSH_PENGYOS_CONF_ENV += ossh_cv_cflag__fzero_call_used_regs_used=no
endif

ifeq ($(BR2_TOOLCHAIN_HAS_GCC_BUG_110934),y)
OPENSSH_PENGYOS_CONF_OPTS += --without-hardening
endif

ifeq ($(BR2_TOOLCHAIN_SUPPORTS_PIE),)
OPENSSH_PENGYOS_CONF_OPTS += --without-pie
endif

OPENSSH_PENGYOS_DEPENDENCIES = host-pkgconf zlib openssl

# crypt() in libcrypt only required for sshd.
ifeq ($(BR2_PACKAGE_LIBXCRYPT),y)
OPENSSH_PENGYOS_DEPENDENCIES += libxcrypt
endif

ifeq ($(BR2_PACKAGE_CRYPTODEV_LINUX),y)
OPENSSH_PENGYOS_DEPENDENCIES += cryptodev-linux
OPENSSH_PENGYOS_CONF_OPTS += --with-ssl-engine
else
OPENSSH_PENGYOS_CONF_OPTS += --without-ssl-engine
endif

ifeq ($(BR2_PACKAGE_AUDIT),y)
OPENSSH_PENGYOS_DEPENDENCIES += audit
OPENSSH_PENGYOS_CONF_OPTS += --with-audit=linux
else
OPENSSH_PENGYOS_CONF_OPTS += --without-audit
endif

ifeq ($(BR2_PACKAGE_LINUX_PAM),y)
define OPENSSH_PENGYOS_INSTALL_PAM_CONF
	$(INSTALL) -D -m 644 $(@D)/contrib/sshd.pam.generic $(TARGET_DIR)/etc/pam.d/sshd
	$(SED) '\%password   required     /lib/security/pam_cracklib.so%d' $(TARGET_DIR)/etc/pam.d/sshd
	$(SED) 's/\#UsePAM no/UsePAM yes/' $(TARGET_DIR)/etc/ssh/sshd_config
endef

OPENSSH_PENGYOS_DEPENDENCIES += linux-pam
OPENSSH_PENGYOS_CONF_OPTS += --with-pam
OPENSSH_PENGYOS_POST_INSTALL_TARGET_HOOKS += OPENSSH_PENGYOS_INSTALL_PAM_CONF
else
OPENSSH_PENGYOS_CONF_OPTS += --without-pam
endif

ifeq ($(BR2_PACKAGE_LIBSELINUX),y)
OPENSSH_PENGYOS_DEPENDENCIES += libselinux
OPENSSH_PENGYOS_CONF_OPTS += --with-selinux
else
OPENSSH_PENGYOS_CONF_OPTS += --without-selinux
endif

# sshd's privilege-separation user. Buildroot's sysusers/unit files are
# top-dir-relative for a stock package, so this package names its own copies in
# the external tree - the same variable the defconfigs use for board files.
ifeq ($(BR2_PACKAGE_SYSTEMD_SYSUSERS),y)
define OPENSSH_PENGYOS_INSTALL_SYSTEMD_SYSUSERS
	$(INSTALL) -m 0644 -D $(BR2_EXTERNAL_PENGYOS_PATH)/package/openssh-pengyos/sshd-sysusers.conf \
		$(TARGET_DIR)/usr/lib/sysusers.d/sshd.conf
endef
else
define OPENSSH_PENGYOS_USERS
	sshd -1 sshd -1 * /var/empty - - SSH drop priv user
endef
endif

# Let the default install rule only install the configuration file.
# The programs will be installed based on the config options selected.
OPENSSH_PENGYOS_INSTALL_TARGET_OPTS = DESTDIR=$(TARGET_DIR) install-sysconf

ifeq ($(BR2_PACKAGE_OPENSSH_PENGYOS_CLIENT),y)
define OPENSSH_PENGYOS_INSTALL_CLIENT_PROGRAMS
	$(INSTALL) -D -m 0755 $(@D)/ssh $(TARGET_DIR)/usr/bin/ssh
	$(INSTALL) -D -m 0755 $(@D)/scp $(TARGET_DIR)/usr/bin/scp
	$(INSTALL) -D -m 0755 $(@D)/sftp $(TARGET_DIR)/usr/bin/sftp
	$(INSTALL) -D -m 0755 $(@D)/ssh-agent $(TARGET_DIR)/usr/bin/ssh-agent
	$(INSTALL) -D -m 0755 $(@D)/ssh-add $(TARGET_DIR)/usr/bin/ssh-add
	$(INSTALL) -D -m 4711 $(@D)/ssh-keysign $(TARGET_DIR)/usr/libexec/ssh-keysign
	$(INSTALL) -D -m 0755 $(@D)/ssh-pkcs11-helper $(TARGET_DIR)/usr/libexec/ssh-pkcs11-helper
	$(INSTALL) -D -m 0755 $(@D)/contrib/ssh-copy-id $(TARGET_DIR)/usr/bin/ssh-copy-id
endef
OPENSSH_PENGYOS_POST_INSTALL_TARGET_HOOKS += OPENSSH_PENGYOS_INSTALL_CLIENT_PROGRAMS
endif

ifeq ($(BR2_PACKAGE_OPENSSH_PENGYOS_SERVER),y)
define OPENSSH_PENGYOS_INSTALL_SERVER_PROGRAMS
	$(INSTALL) -D -m 0755 $(@D)/sshd $(TARGET_DIR)/usr/sbin/sshd
	$(INSTALL) -D -m 0755 $(@D)/sshd-session $(TARGET_DIR)/usr/libexec/sshd-session
	$(INSTALL) -D -m 0755 $(@D)/sftp-server $(TARGET_DIR)/usr/libexec/sftp-server
	$(INSTALL) -D -m 0755 $(@D)/sshd-auth $(TARGET_DIR)/usr/libexec/sshd-auth
endef
OPENSSH_PENGYOS_POST_INSTALL_TARGET_HOOKS += OPENSSH_PENGYOS_INSTALL_SERVER_PROGRAMS

define OPENSSH_PENGYOS_INSTALL_INIT_SYSTEMD
	$(INSTALL) -D -m 644 $(BR2_EXTERNAL_PENGYOS_PATH)/package/openssh-pengyos/sshd.service \
		$(TARGET_DIR)/usr/lib/systemd/system/sshd.service
	$(OPENSSH_PENGYOS_INSTALL_SYSTEMD_SYSUSERS)
endef

define OPENSSH_PENGYOS_INSTALL_INIT_SYSV
	$(INSTALL) -D -m 755 $(BR2_EXTERNAL_PENGYOS_PATH)/package/openssh-pengyos/S50sshd \
		$(TARGET_DIR)/etc/init.d/S50sshd
endef
endif

ifeq ($(BR2_PACKAGE_OPENSSH_PENGYOS_KEY_UTILS),y)
define OPENSSH_PENGYOS_INSTALL_KEY_UTILS
	$(INSTALL) -D -m 0755 $(@D)/ssh-keygen $(TARGET_DIR)/usr/bin/ssh-keygen
	$(INSTALL) -D -m 0755 $(@D)/ssh-keyscan $(TARGET_DIR)/usr/bin/ssh-keyscan
endef
OPENSSH_PENGYOS_POST_INSTALL_TARGET_HOOKS += OPENSSH_PENGYOS_INSTALL_KEY_UTILS
endif

$(eval $(autotools-package))
