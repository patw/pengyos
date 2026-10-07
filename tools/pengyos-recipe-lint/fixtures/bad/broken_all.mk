#
# broken_all — BAD
#
# A recipe that is missing everything the linter checks:
#   * no _VERSION            -> fails version_pinned
#   * no .hash sibling       -> fails hash_present
#   * no _LICENSE            -> fails license_declared
# (there is deliberately no bad/broken_all.hash on disk)
#
BROKEN_ALL_SITE = https://example.com/broken
