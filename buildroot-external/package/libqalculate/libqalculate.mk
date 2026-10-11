################################################################################
#
# libqalculate
#
################################################################################

LIBQALCULATE_VERSION = 5.12.0
LIBQALCULATE_SITE = https://github.com/Qalculate/libqalculate/releases/download/v$(LIBQALCULATE_VERSION)
LIBQALCULATE_LICENSE = GPL-2.0+
LIBQALCULATE_LICENSE_FILES = COPYING
LIBQALCULATE_DEPENDENCIES = host-pkgconf gmp mpfr libxml2 $(TARGET_NLS_DEPENDENCIES)
# qalc (the "text port") is the point; no tests, and no ICU (case-insensitive
# Unicode names only; ICU is ~30 MB the image does not otherwise carry).
LIBQALCULATE_CONF_OPTS = \
	--enable-textport \
	--disable-tests \
	--disable-unittests \
	--disable-defs2doc \
	--without-icu

ifeq ($(BR2_SYSTEM_ENABLE_NLS),)
LIBQALCULATE_CONF_OPTS += --disable-nls
endif

# Exchange rates for currency conversion ("= 10 USD to EUR").
ifeq ($(BR2_PACKAGE_LIBCURL),y)
LIBQALCULATE_DEPENDENCIES += libcurl
LIBQALCULATE_CONF_OPTS += --with-libcurl
else
LIBQALCULATE_CONF_OPTS += --without-libcurl
endif

ifeq ($(BR2_PACKAGE_READLINE),y)
LIBQALCULATE_DEPENDENCIES += readline
LIBQALCULATE_CONF_OPTS += --with-readline
else
LIBQALCULATE_CONF_OPTS += --without-readline
endif

$(eval $(autotools-package))
