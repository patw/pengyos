#
# missing_hash — BAD
#
# A well-formed recipe that simply has NO .hash sibling.
# Fails ONLY hash_present (version + license are fine).
#
# NOTE: there is deliberately no bad/missing_hash.hash on disk.
#
BAD_NOHASH_VERSION = 1.2.3
BAD_NOHASH_SITE = https://example.com/nohash
BAD_NOHASH_SOURCE = nohash-1.2.3.tar.gz
BAD_NOHASH_LICENSE = Apache-2.0
