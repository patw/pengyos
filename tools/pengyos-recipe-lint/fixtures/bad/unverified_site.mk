#
# unverified_site — BAD
#
# A well-formed recipe whose _SITE is an insecure http:// URL rather than a
# pinned https mirror, so it fails ONLY no_unverified_downloads.
# Everything else (version, hash, license) is well-formed.
#
BAD_UNVERIFIED_SITE_VERSION = 1.2.3
BAD_UNVERIFIED_SITE = http://example.com/unverified
BAD_UNVERIFIED_SITE_SOURCE = unverified-1.2.3.tar.gz
BAD_UNVERIFIED_SITE_LICENSE = MIT
