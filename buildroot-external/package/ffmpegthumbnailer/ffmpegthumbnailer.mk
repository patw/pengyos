################################################################################
#
# ffmpegthumbnailer
#
################################################################################

FFMPEGTHUMBNAILER_VERSION = 2.3.1
FFMPEGTHUMBNAILER_SITE = $(call github,dirkvdb,ffmpegthumbnailer,v$(FFMPEGTHUMBNAILER_VERSION))
FFMPEGTHUMBNAILER_LICENSE = GPL-2.0+
FFMPEGTHUMBNAILER_LICENSE_FILES = COPYING
FFMPEGTHUMBNAILER_DEPENDENCIES = ffmpeg libpng jpeg
FFMPEGTHUMBNAILER_CONF_OPTS = \
	-DENABLE_THUMBNAILER=ON \
	-DENABLE_TESTS=OFF \
	-DENABLE_GIO=OFF \
	-DENABLE_STATIC=OFF

$(eval $(cmake-package))
