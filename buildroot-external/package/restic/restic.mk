################################################################################
#
# restic
#
################################################################################

RESTIC_VERSION = 0.19.1
RESTIC_SITE = $(call github,restic,restic,v$(RESTIC_VERSION))
RESTIC_GOMOD = github.com/restic/restic
RESTIC_LICENSE = BSD-2-Clause
RESTIC_LICENSE_FILES = LICENSE
# NVD vendor:product, so pkg-stats can match CVEs.
RESTIC_CPE_ID_VENDOR = restic
# The one binary; the repository also holds helper tools and docs generators.
RESTIC_BUILD_TARGETS = cmd/restic
# restic reports this version (restic version, the repository's own records).
RESTIC_LDFLAGS = -X main.version=$(RESTIC_VERSION)
# Pure Go (its FUSE mount is bazil.org/fuse, not libfuse): no cgo, so the cross
# build needs no target C toolchain.
RESTIC_GO_ENV = CGO_ENABLED=no

$(eval $(golang-package))
