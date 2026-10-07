#
# wrong_source_hash — BAD
#
# The .hash has a sha256, but it is for a *different* filename than the
# recipe's _SOURCE, so the source is not actually covered.
# Fails ONLY hash_present (version + license are fine).
#
BAD_WRONGSRC_VERSION = 2.0.0
BAD_WRONGSRC_SITE = https://example.com/wrongsrc
BAD_WRONGSRC_SOURCE = wrongsrc-2.0.0.tar.gz
BAD_WRONGSRC_LICENSE = GPL-2.0-only
