################################################################################
#
# gst1-libav-pengyos - the gst-libav plugin, built against ffmpeg-pengyos.
#
# Why this file exists: divergence.toml -> ffmpeg-9.0.2-fork. Buildroot's
# gst1-libav names stock ffmpeg three ways: `select BR2_PACKAGE_FFMPEG` in its
# Config.in (a Kconfig select outranks any defconfig line, so it forces
# BR2_PACKAGE_FFMPEG=y and builds ffmpeg 6.1.5 - the mosh pattern), a
# `DEPENDENCIES = ... ffmpeg ...` that links it against the stock libraries, and
# a `select BR2_PACKAGE_FFMPEG_SWSCALE`. Leaving it in the tree with the fork
# present means two packages installing the same libavcodec into the image and
# pkg-stats reporting the vulnerable version as present, which is the failure
# the openssh fork recorded as its cost.
#
# A copy of package/gstreamer1/gst1-libav from Buildroot 2026.08 with the
# prefix renamed and the ffmpeg reference moved to the fork. Same version,
# same source, same hash - the plugin is not forked, only its dependency.
#
################################################################################

GST1_LIBAV_PENGYOS_VERSION = 1.24.13
GST1_LIBAV_PENGYOS_SOURCE = gst-libav-$(GST1_LIBAV_PENGYOS_VERSION).tar.xz
GST1_LIBAV_PENGYOS_SITE = https://gstreamer.freedesktop.org/src/gst-libav
GST1_LIBAV_PENGYOS_LICENSE = LGPL-2.1+
GST1_LIBAV_PENGYOS_LICENSE_FILES = COPYING
GST1_LIBAV_PENGYOS_DEPENDENCIES =  host-pkgconf ffmpeg-pengyos gstreamer1 gst1-plugins-base
GST1_LIBAV_PENGYOS_CONF_OPTS = -Ddoc=disabled

$(eval $(meson-package))
