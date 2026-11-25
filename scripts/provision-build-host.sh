#!/usr/bin/env bash
# Provision a PengyOS build host (Ubuntu/Debian with apt).
#
#   scripts/provision-build-host.sh             # Core + Server + boot testing
#   scripts/provision-build-host.sh --desktop   # + what the Desktop takes from the host
#
# Buildroot builds its own toolchain, so Core and Server only need its host
# prerequisites. The Desktop is different (SPEC.md §7): our libcosmic apps are
# built with cargo on the host (scripts/build-cosmic.sh) against the host's -dev
# packages, and the cosmic package's bindgen loads the host's libclang.
# Nothing is copied from the host into the image: every library the host-built
# binaries link must come from a Buildroot package, and
# board/common/check-needed-libs.py fails the build when one does not. --desktop
# installs exactly the -dev packages those cargo builds compile against. The
# verified host is Ubuntu 26.04; on another release a cargo build may resolve
# against different headers than the image's packages provide.
#
# Idempotent; never runs `apt upgrade`. Needs sudo for apt-get -- run it as the build
# user (it calls sudo itself). The per-user tools (rustup, uv) are installed into that
# user's home, never root's: see BUILD_HOME below.
set -euo pipefail

DESKTOP=0
[ "${1:-}" = "--desktop" ] && DESKTOP=1

PKGS=(
  # Buildroot host requirements
  build-essential git wget curl cpio rsync unzip bc file patch gawk sed
  bison flex gettext texinfo perl python3 python3-dev
  libncurses-dev libssl-dev libelf-dev
  tar gzip bzip2 xz-utils zstd ca-certificates
  # rtkit's meson setup needs xxd (Desktop). Hosts with vim had it; a fresh
  # ubuntu:26.04 box does not (behopc clean rebuild, 2026-10-26).
  xxd
  # Boot testing (tools/pengyos-qemu-verify): QEMU+KVM, UEFI firmware, a software TPM
  qemu-system-x86 ovmf swtpm
  # The Desktop session check (pengyos-qemu-verify desktop): virtual X + screenshot;
  # qemu-system-gui carries the SDL display the session check opens on that X
  # (without it QEMU exits with "Display 'sdl' is not available")
  xvfb ffmpeg qemu-system-gui
  # Repo tooling: tool test suites, the gate
  python3-pytest
  # pengyos-firewall's tests check the shipped ruleset, plus an example router
  # bundle, with the real parser (`unshare -rn nft -c -f`), no root needed
  nftables
  # pengyos-release (signing the download manifests), the nightly's decision page,
  # and the Server tools that read YAML. behoserver had these by hand; a fresh
  # ubuntu:26.04 box has none of them (behopc, 2026-10-26).
  python3-cryptography python3-flask python3-yaml
)

DESKTOP_PKGS=(
  # Building our libcosmic apps (scripts/build-cosmic.sh); libclang for bindgen
  pkg-config cmake clang libclang-dev nasm
  libxkbcommon-dev libwayland-dev libinput-dev libgbm-dev libdrm-dev libegl-dev
  libseat-dev libsystemd-dev libudev-dev libdbus-1-dev libpam0g-dev
  libpipewire-0.3-dev libdisplay-info-dev libgudev-1.0-dev libmtdev-dev libwacom-dev
  libexpat1-dev libfontconfig-dev libfreetype-dev libpixman-1-dev libglib2.0-dev
  libflatpak-dev libostree-dev
  libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libdav1d-dev
  # Nothing is copied from the host into the image any more (post-build used to
  # copy libdisplay-info, OpenSSL, curl and more; 2026-10-23). The -dev packages
  # above pull in the runtime libraries cargo links against.
)
[ "${DESKTOP}" = 1 ] && PKGS+=("${DESKTOP_PKGS[@]}")

# WHOSE HOME the per-user tools go into. apt needs sudo, but rustup and uv have to be
# visible to the build, and every consumer runs with an explicit HOME -- not root's:
# pengyos-box starts the nightly with
#   env -i HOME=/home/beholder PATH=$HOME/.local/bin:$HOME/.cargo/bin:...
# and build-cosmic.sh prepends ~/.cargo/bin itself. A toolchain installed into /root
# because this script was run under sudo is therefore INVISIBLE to both. Measured on
# behoserver, 2026-11-24: the gate's cargo tests and the nightly's pkg-stats both said
# "cargo: not found" while /root/.cargo/bin/rustup sat in the same box, and the nightly
# still wrote a plan -- an empty one -- instead of failing.
BUILD_USER="${SUDO_USER:-$(id -un)}"
BUILD_HOME="${PENGYOS_BUILD_HOME:-$(getent passwd "${BUILD_USER}" | cut -d: -f6)}"
if [ -z "${BUILD_HOME}" ] || [ "/" = "${BUILD_HOME}" ]; then
    echo "[provision] cannot resolve a home directory for ${BUILD_USER}" >&2
    exit 1
fi
if [ "/root" = "${BUILD_HOME}" ] && [ -z "${PENGYOS_BUILD_HOME:-}" ]; then
    echo "[provision] refusing to install rust/uv into /root: the build runs as a" >&2
    echo "  non-root user with HOME set explicitly (see the comment above), so it" >&2
    echo "  would never see them. Run this as the build user -- it calls sudo itself --" >&2
    echo "  or set PENGYOS_BUILD_HOME=/home/<user> if the build really runs as root." >&2
    exit 1
fi
# Run a command as that user with that user's HOME, whoever invoked this script.
as_build_user() {
    if [ "$(id -un)" = "${BUILD_USER}" ]; then
        HOME="${BUILD_HOME}" "$@"
    else
        runuser -u "${BUILD_USER}" -- env HOME="${BUILD_HOME}" "$@"
    fi
}

echo "[provision] apt-get update"
sudo apt-get update -qq
echo "[provision] installing ${#PKGS[@]} packages (no upgrade)"
sudo apt-get install -y --no-install-recommends "${PKGS[@]}"

if [ "${DESKTOP}" = 1 ]; then
    # Some COSMIC components need a newer rustc than the distribution ships.
    # rustup installs per-user into ~/.cargo without touching PATH;
    # build-cosmic.sh puts ~/.cargo/bin first itself.
    RUST_VERSION="${PENGYOS_RUST_VERSION:-1.95}"
    if [ ! -x "${BUILD_HOME}/.cargo/bin/rustup" ]; then
        echo "[provision] installing rustup (toolchain ${RUST_VERSION}) for ${BUILD_USER} into ${BUILD_HOME}/.cargo"
        tmp_rustup="$(mktemp)"
        curl -sSf https://sh.rustup.rs -o "${tmp_rustup}"
        chmod 644 "${tmp_rustup}"
        as_build_user sh "${tmp_rustup}" -y --no-modify-path \
            --profile minimal --default-toolchain "${RUST_VERSION}"
        rm -f "${tmp_rustup}"
    else
        as_build_user "${BUILD_HOME}/.cargo/bin/rustup" toolchain install \
            --profile minimal "${RUST_VERSION}"
        as_build_user "${BUILD_HOME}/.cargo/bin/rustup" default "${RUST_VERSION}"
    fi
fi

# uv, for the nightly: Buildroot's pkg-stats is run as
#   uv run -q --with aiohttp --with setuptools --with ijson python3 support/scripts/pkg-stats
# (tools/pengyos-nightly/pengyos_nightly/sources.py). Without it every edition ends
# "exit 127: uv: No such file or directory", the observation records 0 packages, the
# gate goes red and the plan comes out empty -- the act lane then does nothing and looks
# healthy. Same HOME rule as rustup above.
if [ ! -x "${BUILD_HOME}/.local/bin/uv" ]; then
    echo "[provision] installing uv for ${BUILD_USER} into ${BUILD_HOME}/.local/bin"
    tmp_uv="$(mktemp)"
    curl -sSf https://astral.sh/uv/install.sh -o "${tmp_uv}"
    chmod 644 "${tmp_uv}"
    as_build_user env INSTALLER_NO_MODIFY_PATH=1 sh "${tmp_uv}"
    rm -f "${tmp_uv}"
fi

echo "[provision] done. versions:"
printf '  qemu:  %s\n' "$(qemu-system-x86_64 --version | head -1)"
printf '  gcc:   %s\n' "$(gcc --version | head -1)"
[ "${DESKTOP}" = 1 ] && printf '  rustc: %s\n' "$("${BUILD_HOME}/.cargo/bin/rustc" --version)"
[ -x "${BUILD_HOME}/.local/bin/uv" ] && printf '  uv:    %s\n' "$("${BUILD_HOME}/.local/bin/uv" --version)"
if [ -w /dev/kvm ]; then
    echo "  /dev/kvm is writable by $(id -un) — KVM OK"
else
    echo "  WARNING: /dev/kvm is not writable by $(id -un) — add yourself to the kvm group, or QEMU falls back to TCG (slow)"
fi
