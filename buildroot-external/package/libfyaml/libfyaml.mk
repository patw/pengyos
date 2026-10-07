################################################################################
#
# libfyaml
#
################################################################################

LIBFYAML_VERSION = 0.9.6
LIBFYAML_SITE = https://github.com/pantoniou/libfyaml/releases/download/v$(LIBFYAML_VERSION)
LIBFYAML_LICENSE = MIT
LIBFYAML_LICENSE_FILES = LICENSE
LIBFYAML_INSTALL_STAGING = YES
LIBFYAML_DEPENDENCIES = host-pkgconf
# --disable-network: its test suite would fetch the YAML test corpus.
LIBFYAML_CONF_OPTS = --disable-network

$(eval $(autotools-package))
