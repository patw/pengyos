################################################################################
#
# llama-cpp-vk — pinned, Vulkan-enabled llama-server with /v1/systemone
#
# PROTOTYPE SKELETON — not installed, not sourced from Config.in/external.mk.
# See recipe/README.md for the two Buildroot gaps (host-shaderc, spirv-headers)
# that must close before this builds.
#
################################################################################

# b11318 is the first release tag after the /v1/systemone decision API landed
# (2026-10-02, commit a4cb4c61f). Measured build: b76b133 (2026-10-15).
LLAMA_CPP_VK_VERSION = b11318
LLAMA_CPP_VK_SOURCE = $(LLAMA_CPP_VK_VERSION).tar.gz
LLAMA_CPP_VK_SITE = https://github.com/ggml-org/llama.cpp/archive/refs/tags
LLAMA_CPP_VK_LICENSE = MIT
LLAMA_CPP_VK_LICENSE_FILES = LICENSE
LLAMA_CPP_VK_CPE_ID_VENDOR = ggml
LLAMA_CPP_VK_CPE_ID_PRODUCT = llama.cpp
LLAMA_CPP_VK_INSTALL_STAGING = NO

# Static: no libllama.so / libggml.so to ship (measured 2026-10-15: the
# resulting llama-server links only libvulkan, OpenSSL, libstdc++/gcc/libc,
# zlib/zstd). PengyOS has no libgomp in the rootfs, so OpenMP is OFF.
LLAMA_CPP_VK_CONF_OPTS = \
	-DBUILD_SHARED_LIBS=OFF \
	-DGGML_VULKAN=ON \
	-DGGML_CUDA=OFF \
	-DGGML_OPENMP=OFF \
	-DLLAMA_CURL=OFF \
	-DLLAMA_BUILD_TESTS=OFF \
	-DLLAMA_BUILD_EXAMPLES=OFF \
	-DLLAMA_BUILD_SERVER=ON

LLAMA_CPP_VK_DEPENDENCIES += vulkan-loader host-glslc spirv-headers

$(eval $(cmake-package))
