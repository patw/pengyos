################################################################################
#
# ffmpegthumbnailer
#
################################################################################

FFMPEGTHUMBNAILER_VERSION = 2.3.1
FFMPEGTHUMBNAILER_SITE = $(call github,dirkvdb,ffmpegthumbnailer,v$(FFMPEGTHUMBNAILER_VERSION))
FFMPEGTHUMBNAILER_LICENSE = GPL-2.0+
FFMPEGTHUMBNAILER_LICENSE_FILES = COPYING
# ffmpeg-pengyos, not ffmpeg: the fork is what ships (divergence.toml ->
# ffmpeg-9.0.2-fork). Building this against the stock package would pull ffmpeg
# 6.1.5 back into the image beside the fork - two packages installing the same
# libavcodec, and pkg-stats reporting the vulnerable version as present.
FFMPEGTHUMBNAILER_DEPENDENCIES = ffmpeg-pengyos libpng jpeg
FFMPEGTHUMBNAILER_CONF_OPTS = \
	-DENABLE_THUMBNAILER=ON \
	-DENABLE_TESTS=OFF \
	-DENABLE_GIO=OFF \
	-DENABLE_STATIC=OFF

$(eval $(cmake-package))
