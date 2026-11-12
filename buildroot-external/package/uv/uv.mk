################################################################################
#
# uv
#
################################################################################

# On the host since 26.11 (Pengy's Server field review, R4, 2026-11-03): the
# per-app venv recipe (USE-CASES US-SRV-10) needs it, and until then it came only
# from the dev guest's installer in ~/.local/bin, which is not on the host's PATH.
# Built from source like every other package (docs/principles.md).
UV_VERSION = 0.13.7
UV_SITE = $(call github,astral-sh,uv,$(UV_VERSION))
UV_LICENSE = Apache-2.0 or MIT
UV_LICENSE_FILES = LICENSE-APACHE LICENSE-MIT

# The binary crate is crates/uv in a Cargo workspace; it installs uv and uvx.
# Default features only: uv's packaging notes ask distributions to leave the
# `self-update` feature off (updates come with the image), and it is not a
# default. rust-toolchain.toml pins a newer rustc for uv's own CI; Buildroot's
# cargo does not read it, and uv's rust-version (1.97.0) is what must be met.
UV_SUBDIR = crates/uv

# CPE (NVD vendor:product) so pkg-stats can match CVEs; verified against NVD 2026-11-12.
UV_CPE_ID_VENDOR = astral

$(eval $(cargo-package))
