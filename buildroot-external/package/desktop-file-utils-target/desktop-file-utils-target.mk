################################################################################
#
# desktop-file-utils-target
#
################################################################################

# Same source as Buildroot's host-only desktop-file-utils recipe.
DESKTOP_FILE_UTILS_TARGET_VERSION = 0.26
DESKTOP_FILE_UTILS_TARGET_SOURCE = desktop-file-utils-$(DESKTOP_FILE_UTILS_TARGET_VERSION).tar.xz
DESKTOP_FILE_UTILS_TARGET_SITE = https://www.freedesktop.org/software/desktop-file-utils/releases
DESKTOP_FILE_UTILS_TARGET_LICENSE = GPL-2.0+
DESKTOP_FILE_UTILS_TARGET_LICENSE_FILES = COPYING
# host-desktop-file-utils: board/desktop/post-build.sh builds the image's MIME
# index (mimeinfo.cache) with it, rather than with whatever the build host has.
DESKTOP_FILE_UTILS_TARGET_DEPENDENCIES = host-pkgconf libglib2 host-desktop-file-utils

$(eval $(meson-package))
