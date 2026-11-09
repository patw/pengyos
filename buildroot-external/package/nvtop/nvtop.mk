################################################################################
#
# nvtop
#
################################################################################

NVTOP_VERSION = 3.4.0
NVTOP_SITE = $(call github,Syllo,nvtop,$(NVTOP_VERSION))
NVTOP_LICENSE = GPL-3.0+
NVTOP_LICENSE_FILES = COPYING
NVTOP_DEPENDENCIES = ncurses libdrm systemd
# The x86 GPUs PengyOS runs on. NVML is dlopened (libnvidia-ml.so.1), so
# NVIDIA support needs nothing at build time and stays quiet without the
# driver; libdrm and libdrm_amdgpu are dlopened too. The rest are ARM SoC or
# vendor-library backends with nothing to find on these machines.
NVTOP_CONF_OPTS = \
	-DNVIDIA_SUPPORT=ON \
	-DAMDGPU_SUPPORT=ON \
	-DINTEL_SUPPORT=ON \
	-DMSM_SUPPORT=OFF \
	-DAPPLE_SUPPORT=OFF \
	-DPANFROST_SUPPORT=OFF \
	-DPANTHOR_SUPPORT=OFF \
	-DASCEND_SUPPORT=OFF \
	-DV3D_SUPPORT=OFF \
	-DTPU_SUPPORT=OFF \
	-DROCKCHIP_SUPPORT=OFF \
	-DMETAX_SUPPORT=OFF \
	-DENFLAME_SUPPORT=OFF \
	-DUSE_LIBUDEV_OVER_LIBSYSTEMD=OFF \
	-DBUILD_TESTING=OFF

$(eval $(cmake-package))
