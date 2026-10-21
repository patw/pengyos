################################################################################
#
# ffmpeg-pengyos - PengyOS's fork of the stock ffmpeg package.
#
# Why this file exists: divergence.toml -> ffmpeg-9.0.2-fork. Buildroot 2026.08
# pins ffmpeg 6.1.5; 25 P0/P1 CVE matches sit inside it and upstream is at
# 9.0.2. Seventeen of the twenty-five name no upstream commit at all, so no
# cherry-pick set reaches them, and a br2-external tree cannot move a stock
# package's version (proven 2026-10-05, recorded in
# tools/pengyos-nightly/pengyos_nightly/edits.py). The fix is the version, so
# the shape left is a named fork with an expiry - the same decision as
# openssh-pengyos, and the same discipline: it is deleted when Buildroot ships
# a version that closes these CVEs.
#
# A faithful copy of package/ffmpeg/ffmpeg.mk from Buildroot 2026.08 with the
# version moved to 9.0.2 and every symbol prefixed (FFMPEG_ -> FFMPEG_PENGYOS_,
# BR2_PACKAGE_FFMPEG -> BR2_PACKAGE_FFMPEG_PENGYOS). Read it against that file;
# nothing else was changed.
#
# ffmpeg is a library, not only a command: pipewire and gst1-libav link against
# it and ffmpegthumbnailer (our recipe) depends on it, so the dependents are
# redirected with it. See the ledger entry for the cost that buys.
#
################################################################################

FFMPEG_PENGYOS_VERSION = 9.0.2
FFMPEG_PENGYOS_SOURCE = ffmpeg-$(FFMPEG_PENGYOS_VERSION).tar.xz
FFMPEG_PENGYOS_SITE = https://ffmpeg.org/releases
FFMPEG_PENGYOS_INSTALL_STAGING = YES

FFMPEG_PENGYOS_LICENSE = LGPL-2.1+, libjpeg license
FFMPEG_PENGYOS_LICENSE_FILES = LICENSE.md COPYING.LGPLv2.1
ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_GPL),y)
FFMPEG_PENGYOS_LICENSE += and GPL-2.0+
FFMPEG_PENGYOS_LICENSE_FILES += COPYING.GPLv2
endif

FFMPEG_PENGYOS_CPE_ID_VENDOR = ffmpeg
# The upstream product name, not our package name. pkg-stats builds the CPE from
# VENDOR:PRODUCT:VERSION (package/pkg-generic.mk:745) and defaults PRODUCT to the
# recipe's RAWNAME; left at the default this package is
# cpe:2.3:a:ffmpeg:ffmpeg-pengyos:9.0.2, which matches nothing in NVD - the fork
# would carry ffmpeg and be told it has no CVEs, forever, including the next one.
# The openssh fork hit exactly this on its first landing.
FFMPEG_PENGYOS_CPE_ID_PRODUCT = ffmpeg

# Two upstream master fixes carried on 9.0.2 (ffmpeg 9.0.3 is not released). Both are
# NVD's own fix references and apply unmodified; recorded in divergence.toml
# (ffmpeg-9.0.2-fork). Declared here because a fork's patches live in its recipe
# directory, not under BR2_GLOBAL_PATCH_DIR.
# 0001-avutil-encryption_info-avoid-stack-exhaustion-freeing-init-info-list.patch
FFMPEG_PENGYOS_IGNORE_CVES += CVE-2026-107678
# 0002-avformat-rtsp-limit-the-number-of-redirects.patch
FFMPEG_PENGYOS_IGNORE_CVES += CVE-2026-107696

FFMPEG_PENGYOS_CONF_OPTS = \
	--prefix=/usr \
	--enable-avfilter \
	--disable-version3 \
	--enable-logging \
	--enable-optimizations \
	--disable-extra-warnings \
	--enable-avdevice \
	--enable-avcodec \
	--enable-avformat \
	--enable-network \
	--disable-gray \
	--enable-swscale-alpha \
	--disable-small \
	--disable-dxva2 \
	--enable-runtime-cpudetect \
	--disable-hardcoded-tables \
	--disable-mipsdsp \
	--disable-mipsdspr2 \
	--disable-msa \
	--enable-hwaccels \
	--disable-cuvid \
	--disable-nvenc \
	--disable-avisynth \
	--disable-frei0r \
	--disable-libopencore-amrnb \
	--disable-libopencore-amrwb \
	--disable-libdc1394 \
	--disable-libgsm \
	--disable-libilbc \
	--disable-libvo-amrwbenc \
	--disable-symver \
	--disable-doc \
	--disable-mmal

# Deviations from package/ffmpeg/ffmpeg.mk (Buildroot 2026.08), each one an option
# ffmpeg 9.0.2 no longer accepts - `configure` aborts with "Unknown option", so a
# faithful copy of the 6.1.5 recipe does not configure at 9.0.2. Measured against
# 9.0.2's own `configure --help`; six of the 127 options the recipe passes are gone:
#   --disable-crystalhd  the CrystalHD decoder was removed upstream
#   --disable-cuda       CUDA codec support went; what remains is cuda-nvcc (defaults
#                        to no) and cuda-llvm, so the intent - no CUDA - holds without it
#   --disable-omx        OpenMAX support was removed upstream
#   --disable-omx-rpi    likewise
#   --disable-postproc   libpostproc was removed upstream; see the block below
#   --enable-fontconfig  renamed to --enable-libfontconfig; see the block below
# Nothing else in this file differs from the stock recipe.

FFMPEG_PENGYOS_DEPENDENCIES += host-pkgconf

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_GPL),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-gpl
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-gpl
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_NONFREE),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-nonfree
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-nonfree
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_FFMPEG),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-ffmpeg
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-ffmpeg
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_FFPLAY),y)
FFMPEG_PENGYOS_DEPENDENCIES += sdl2
FFMPEG_PENGYOS_CONF_OPTS += --enable-ffplay
FFMPEG_PENGYOS_CONF_ENV += SDL_CONFIG=$(STAGING_DIR)/usr/bin/sdl2-config
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-ffplay
endif

ifeq ($(BR2_PACKAGE_JACK1),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libjack
FFMPEG_PENGYOS_DEPENDENCIES += jack1
else ifeq ($(BR2_PACKAGE_JACK2),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libjack
FFMPEG_PENGYOS_DEPENDENCIES += jack2
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libjack
endif

ifeq ($(BR2_PACKAGE_PULSEAUDIO),y)
FFMPEG_PENGYOS_DEPENDENCIES += pulseaudio
FFMPEG_PENGYOS_CONF_OPTS += --enable-libpulse
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libpulse
endif

ifeq ($(BR2_PACKAGE_LIBV4L),y)
FFMPEG_PENGYOS_DEPENDENCIES += libv4l
FFMPEG_PENGYOS_CONF_OPTS += --enable-libv4l2
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libv4l2
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_FFPROBE),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-ffprobe
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-ffprobe
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_XCBGRAB),y)
FFMPEG_PENGYOS_CONF_OPTS += \
	--enable-libxcb \
	--enable-libxcb-shape \
	--enable-libxcb-shm \
	--enable-libxcb-xfixes
FFMPEG_PENGYOS_DEPENDENCIES += libxcb
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libxcb
endif

# libpostproc was removed from ffmpeg upstream, so there is no --enable-postproc to
# pass and no BR2_PACKAGE_FFMPEG_PENGYOS_POSTPROC to honour; the stock recipe's
# conditional here is one of the six options 9.0.2 rejects. The Config.in symbol is
# dropped with it.

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_SWSCALE),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-swscale
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-swscale
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_ENCODERS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-encoders \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_ENCODERS)),--enable-encoder=$(x))
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_DECODERS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-decoders \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_DECODERS)),--enable-decoder=$(x))
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_MUXERS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-muxers \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_MUXERS)),--enable-muxer=$(x))
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_DEMUXERS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-demuxers \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_DEMUXERS)),--enable-demuxer=$(x))
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_PARSERS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-parsers \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_PARSERS)),--enable-parser=$(x))
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_BSFS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-bsfs \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_BSFS)),--enable-bsf=$(x))
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_PROTOCOLS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-protocols \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_PROTOCOLS)),--enable-protocol=$(x))
endif

ifneq ($(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_FILTERS)),all)
FFMPEG_PENGYOS_CONF_OPTS += --disable-filters \
	$(foreach x,$(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_FILTERS)),--enable-filter=$(x))
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_INDEVS),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-indevs
ifeq ($(BR2_PACKAGE_ALSA_LIB),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-alsa
FFMPEG_PENGYOS_DEPENDENCIES += alsa-lib
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-alsa
endif
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-indevs
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_OUTDEVS),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-outdevs
ifeq ($(BR2_PACKAGE_ALSA_LIB),y)
FFMPEG_PENGYOS_DEPENDENCIES += alsa-lib
endif
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-outdevs
endif

ifeq ($(BR2_TOOLCHAIN_HAS_THREADS),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-pthreads
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-pthreads
endif

ifeq ($(BR2_PACKAGE_ZLIB),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-zlib
FFMPEG_PENGYOS_DEPENDENCIES += zlib
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-zlib
endif

ifeq ($(BR2_PACKAGE_BZIP2),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-bzlib
FFMPEG_PENGYOS_DEPENDENCIES += bzip2
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-bzlib
endif

ifeq ($(BR2_PACKAGE_FDK_AAC)$(BR2_PACKAGE_FFMPEG_PENGYOS_NONFREE),yy)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libfdk-aac
FFMPEG_PENGYOS_DEPENDENCIES += fdk-aac
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libfdk-aac
endif

ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_GPL)$(BR2_PACKAGE_LIBCDIO_PARANOIA),yy)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libcdio
FFMPEG_PENGYOS_DEPENDENCIES += libcdio-paranoia
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libcdio
endif

ifeq ($(BR2_PACKAGE_GNUTLS),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-gnutls --disable-openssl
FFMPEG_PENGYOS_DEPENDENCIES += gnutls
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-gnutls
ifeq ($(BR2_PACKAGE_OPENSSL),y)
# openssl isn't license compatible with GPL
ifeq ($(BR2_PACKAGE_FFMPEG_PENGYOS_GPL)x$(BR2_PACKAGE_FFMPEG_PENGYOS_NONFREE),yx)
FFMPEG_PENGYOS_CONF_OPTS += --disable-openssl
else
FFMPEG_PENGYOS_CONF_OPTS += --enable-openssl
FFMPEG_PENGYOS_DEPENDENCIES += openssl
endif
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-openssl
endif
endif

ifeq ($(BR2_PACKAGE_LIBDRM),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libdrm
FFMPEG_PENGYOS_DEPENDENCIES += libdrm
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libdrm
endif

ifeq ($(BR2_PACKAGE_LIBOPENH264),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libopenh264
FFMPEG_PENGYOS_DEPENDENCIES += libopenh264
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libopenh264
endif

ifeq ($(BR2_PACKAGE_LIBVORBIS),y)
FFMPEG_PENGYOS_DEPENDENCIES += libvorbis
FFMPEG_PENGYOS_CONF_OPTS += \
	--enable-libvorbis \
	--enable-muxer=ogg \
	--enable-encoder=libvorbis
endif

ifeq ($(BR2_PACKAGE_LIBVA),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-vaapi
FFMPEG_PENGYOS_DEPENDENCIES += libva
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-vaapi
endif

ifeq ($(BR2_PACKAGE_LIBVDPAU),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-vdpau
FFMPEG_PENGYOS_DEPENDENCIES += libvdpau
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-vdpau
endif

# To avoid a circular dependency only use opencv if opencv itself does
# not depend on ffmpeg.
ifeq ($(BR2_PACKAGE_OPENCV4_LIB_IMGPROC)x$(BR2_PACKAGE_OPENCV4_WITH_FFMPEG),yx)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libopencv \
	--extra-cflags=-I$(STAGING_DIR)/usr/include/opencv4
FFMPEG_PENGYOS_DEPENDENCIES += opencv4
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libopencv
endif

ifeq ($(BR2_PACKAGE_OPUS),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libopus
FFMPEG_PENGYOS_DEPENDENCIES += opus
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libopus
endif

ifeq ($(BR2_PACKAGE_LIBVPX),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libvpx
FFMPEG_PENGYOS_DEPENDENCIES += libvpx
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libvpx
endif

ifeq ($(BR2_PACKAGE_LIBASS),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libass
FFMPEG_PENGYOS_DEPENDENCIES += libass
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libass
endif

ifeq ($(BR2_PACKAGE_LIBBLURAY),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libbluray
FFMPEG_PENGYOS_DEPENDENCIES += libbluray
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libbluray
endif

ifeq ($(BR2_PACKAGE_LIBVPL),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libvpl --disable-libmfx
FFMPEG_PENGYOS_DEPENDENCIES += libvpl
else ifeq ($(BR2_PACKAGE_INTEL_MEDIASDK),y)
FFMPEG_PENGYOS_CONF_OPTS += --disable-libvpl --enable-libmfx
FFMPEG_PENGYOS_DEPENDENCIES += intel-mediasdk
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libvpl --disable-libmfx
endif

ifeq ($(BR2_PACKAGE_RTMPDUMP),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-librtmp
FFMPEG_PENGYOS_DEPENDENCIES += rtmpdump
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-librtmp
endif

ifeq ($(BR2_PACKAGE_LAME),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libmp3lame
FFMPEG_PENGYOS_DEPENDENCIES += lame
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libmp3lame
endif

ifeq ($(BR2_PACKAGE_LIBMODPLUG),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libmodplug
FFMPEG_PENGYOS_DEPENDENCIES += libmodplug
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libmodplug
endif

ifeq ($(BR2_PACKAGE_LIBOPENMPT),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libopenmpt
FFMPEG_PENGYOS_DEPENDENCIES += libopenmpt
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libopenmpt
endif

ifeq ($(BR2_PACKAGE_LIBSOXR),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libsoxr
FFMPEG_PENGYOS_DEPENDENCIES += libsoxr
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libsoxr
endif

ifeq ($(BR2_PACKAGE_SPEEX),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libspeex
FFMPEG_PENGYOS_DEPENDENCIES += speex
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libspeex
endif

ifeq ($(BR2_PACKAGE_LIBTHEORA),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libtheora
FFMPEG_PENGYOS_DEPENDENCIES += libtheora
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libtheora
endif

ifeq ($(BR2_PACKAGE_LIBICONV),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-iconv
FFMPEG_PENGYOS_DEPENDENCIES += libiconv
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-iconv
endif

ifeq ($(BR2_PACKAGE_LIBXML2),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libxml2
FFMPEG_PENGYOS_DEPENDENCIES += libxml2
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libxml2
endif

# ffmpeg freetype support require fenv.h which is only
# available/working on glibc.
# The microblaze variant doesn't provide the needed exceptions
ifeq ($(BR2_PACKAGE_FREETYPE)$(BR2_TOOLCHAIN_USES_GLIBC)x$(BR2_microblaze),yyx)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libfreetype
FFMPEG_PENGYOS_DEPENDENCIES += freetype
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libfreetype
endif

ifeq ($(BR2_PACKAGE_FONTCONFIG),y)
# --enable-libfontconfig, not --enable-fontconfig: the option was renamed upstream,
# and 9.0.2 rejects the old spelling.
FFMPEG_PENGYOS_CONF_OPTS += --enable-libfontconfig
FFMPEG_PENGYOS_DEPENDENCIES += fontconfig
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libfontconfig
endif

ifeq ($(BR2_PACKAGE_HARFBUZZ),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libharfbuzz
FFMPEG_PENGYOS_DEPENDENCIES += harfbuzz
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libharfbuzz
endif

ifeq ($(BR2_PACKAGE_LIBFRIBIDI),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libfribidi
FFMPEG_PENGYOS_DEPENDENCIES += libfribidi
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libfribidi
endif

ifeq ($(BR2_PACKAGE_OPENJPEG),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libopenjpeg
FFMPEG_PENGYOS_DEPENDENCIES += openjpeg
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libopenjpeg
endif

ifeq ($(BR2_PACKAGE_X264)$(BR2_PACKAGE_FFMPEG_PENGYOS_GPL),yy)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libx264
FFMPEG_PENGYOS_DEPENDENCIES += x264
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libx264
endif

ifeq ($(BR2_PACKAGE_X265)$(BR2_PACKAGE_FFMPEG_PENGYOS_GPL),yy)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libx265
FFMPEG_PENGYOS_DEPENDENCIES += x265
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libx265
endif

ifeq ($(BR2_PACKAGE_DAV1D),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-libdav1d
FFMPEG_PENGYOS_DEPENDENCIES += dav1d
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-libdav1d
endif

ifeq ($(BR2_X86_CPU_HAS_MMX),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-x86asm
FFMPEG_PENGYOS_DEPENDENCIES += host-nasm
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-x86asm
FFMPEG_PENGYOS_CONF_OPTS += --disable-mmx
endif

ifeq ($(BR2_X86_CPU_HAS_SSE),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-sse
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-sse
endif

ifeq ($(BR2_X86_CPU_HAS_SSE2),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-sse2
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-sse2
endif

ifeq ($(BR2_X86_CPU_HAS_SSE3),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-sse3
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-sse3
endif

ifeq ($(BR2_X86_CPU_HAS_SSSE3),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-ssse3
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-ssse3
endif

ifeq ($(BR2_X86_CPU_HAS_SSE4),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-sse4
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-sse4
endif

ifeq ($(BR2_X86_CPU_HAS_SSE42),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-sse42
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-sse42
endif

ifeq ($(BR2_X86_CPU_HAS_AVX),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-avx
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-avx
endif

ifeq ($(BR2_X86_CPU_HAS_AVX2),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-avx2
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-avx2
endif

# Explicitly disable everything that doesn't match for ARM
# FFMPEG "autodetects" by compiling an extended instruction via AS
# This works on compilers that aren't built for generic by default
ifeq ($(BR2_ARM_CPU_ARMV4),y)
FFMPEG_PENGYOS_CONF_OPTS += --disable-armv5te
endif
ifeq ($(BR2_ARM_CPU_ARMV6)$(BR2_ARM_CPU_ARMV7A),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-armv6
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-armv6 --disable-armv6t2
endif
ifeq ($(BR2_ARM_CPU_HAS_VFPV2),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-vfp
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-vfp
endif
ifeq ($(BR2_ARM_CPU_HAS_NEON),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-neon
else ifeq ($(BR2_aarch64),y)
FFMPEG_PENGYOS_CONF_OPTS += --enable-neon
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-neon
endif

ifeq ($(BR2_mips)$(BR2_mipsel)$(BR2_mips64)$(BR2_mips64el),y)
ifeq ($(BR2_MIPS_SOFT_FLOAT),y)
FFMPEG_PENGYOS_CONF_OPTS += --disable-mipsfpu
else
FFMPEG_PENGYOS_CONF_OPTS += --enable-mipsfpu
endif

# Fix build failure on several missing assembly instructions
FFMPEG_PENGYOS_CONF_OPTS += --disable-asm
endif # MIPS

ifeq ($(BR2_POWERPC_CPU_HAS_ALTIVEC):$(BR2_powerpc64le),y:)
FFMPEG_PENGYOS_CONF_OPTS += --enable-altivec
else ifeq ($(BR2_POWERPC_CPU_HAS_VSX):$(BR2_powerpc64le),y:y)
# On LE, ffmpeg AltiVec support needs VSX intrinsics, and VSX
# is an extension to AltiVec.
FFMPEG_PENGYOS_CONF_OPTS += --enable-altivec
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-altivec
endif

# Fix build failure on several missing assembly instructions
ifeq ($(BR2_RISCV_32),y)
FFMPEG_PENGYOS_CONF_OPTS += --disable-rvv --disable-asm
endif

# Uses __atomic_fetch_add_4
ifeq ($(BR2_TOOLCHAIN_HAS_LIBATOMIC),y)
FFMPEG_PENGYOS_CONF_OPTS += --extra-libs=-latomic
endif

ifeq ($(BR2_STATIC_LIBS),)
FFMPEG_PENGYOS_CONF_OPTS += --enable-pic
else
FFMPEG_PENGYOS_CONF_OPTS += --disable-pic
endif

# Default to --cpu=generic for MIPS architecture, in order to avoid a
# warning from ffmpeg's configure script.
ifeq ($(BR2_mips)$(BR2_mipsel)$(BR2_mips64)$(BR2_mips64el),y)
FFMPEG_PENGYOS_CONF_OPTS += --cpu=generic
else ifneq ($(GCC_TARGET_CPU),)
FFMPEG_PENGYOS_CONF_OPTS += --cpu="$(GCC_TARGET_CPU)"
else ifneq ($(GCC_TARGET_ARCH),)
FFMPEG_PENGYOS_CONF_OPTS += --cpu="$(GCC_TARGET_ARCH)"
endif

FFMPEG_PENGYOS_CFLAGS = $(TARGET_CFLAGS)

ifeq ($(BR2_TOOLCHAIN_HAS_GCC_BUG_85180),y)
FFMPEG_PENGYOS_CONF_OPTS += --disable-optimizations
FFMPEG_PENGYOS_CFLAGS += -O0
endif

ifeq ($(BR2_TOOLCHAIN_HAS_GCC_BUG_68485),y)
FFMPEG_PENGYOS_CONF_OPTS += --disable-optimizations
FFMPEG_PENGYOS_CFLAGS += -O0
endif

ifeq ($(BR2_ARM_INSTRUCTIONS_THUMB),y)
FFMPEG_PENGYOS_CFLAGS += -marm
endif

FFMPEG_PENGYOS_CONF_ENV += CFLAGS="$(FFMPEG_PENGYOS_CFLAGS)"
FFMPEG_PENGYOS_CONF_OPTS += $(call qstrip,$(BR2_PACKAGE_FFMPEG_PENGYOS_EXTRACONF))

# Override FFMPEG_PENGYOS_CONFIGURE_CMDS: FFmpeg does not support --target and others
define FFMPEG_PENGYOS_CONFIGURE_CMDS
	(cd $(FFMPEG_PENGYOS_SRCDIR) && rm -rf config.cache && \
	$(TARGET_CONFIGURE_OPTS) \
	$(TARGET_CONFIGURE_ARGS) \
	$(FFMPEG_PENGYOS_CONF_ENV) \
	./configure \
		--enable-cross-compile \
		--cross-prefix=$(TARGET_CROSS) \
		--sysroot=$(STAGING_DIR) \
		--host-cc="$(HOSTCC)" \
		--arch=$(BR2_ARCH) \
		--target-os="linux" \
		--disable-stripping \
		--pkg-config="$(PKG_CONFIG_HOST_BINARY)" \
		$(SHARED_STATIC_LIBS_OPTS) \
		$(FFMPEG_PENGYOS_CONF_OPTS) \
	)
endef

define FFMPEG_PENGYOS_REMOVE_EXAMPLE_SRC_FILES
	rm -rf $(TARGET_DIR)/usr/share/ffmpeg/examples
endef
FFMPEG_PENGYOS_POST_INSTALL_TARGET_HOOKS += FFMPEG_PENGYOS_REMOVE_EXAMPLE_SRC_FILES

$(eval $(autotools-package))
