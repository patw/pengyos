#!/usr/bin/env bash
# Provision a PengyOS build host (Ubuntu/Debian with apt).
#
#   scripts/provision-build-host.sh             # Core + Server + boot testing
#   scripts/provision-build-host.sh --desktop   # + what the Desktop takes from the host
#
# Buildroot builds its own toolchain, so Core and Server only need its host
# prerequisites. The Desktop is different (SPEC.md §7): COSMIC is built with
# cargo on the host (scripts/build-cosmic.sh) against the host's libraries, and
# board/desktop/post-build.sh copies the libraries the COSMIC apps link that the
# image does not build, plus an OpenSSL/curl pair, into the image. --desktop
# installs exactly the packages those come from. The verified host is Ubuntu 26.04; on
# another release the copied libraries' SONAMEs may differ.
#
# Idempotent; never runs `apt upgrade`. Needs sudo for apt-get.
set -euo pipefail

DESKTOP=0
[ "${1:-}" = "--desktop" ] && DESKTOP=1

PKGS=(
  # Buildroot host requirements
  build-essential git wget curl cpio rsync unzip bc file patch gawk sed
  bison flex gettext texinfo perl python3 python3-dev
  libncurses-dev libssl-dev libelf-dev
  tar gzip bzip2 xz-utils zstd ca-certificates
  # Boot testing (tools/pengyos-qemu-verify): QEMU+KVM, UEFI firmware, a software TPM
  qemu-system-x86 ovmf swtpm
  # The Desktop session check (pengyos-qemu-verify desktop): virtual X + screenshot
  xvfb ffmpeg
  # Repo tooling: tool test suites, the gate
  python3-pytest
)

DESKTOP_PKGS=(
  # Building COSMIC and our libcosmic apps (scripts/build-cosmic.sh)
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

echo "[provision] apt-get update"
sudo apt-get update -qq
echo "[provision] installing ${#PKGS[@]} packages (no upgrade)"
sudo apt-get install -y --no-install-recommends "${PKGS[@]}"

if [ "${DESKTOP}" = 1 ]; then
    # Some COSMIC components need a newer rustc than the distribution ships.
    # rustup installs per-user into ~/.cargo without touching PATH;
    # build-cosmic.sh puts ~/.cargo/bin first itself.
    RUST_VERSION="${PENGYOS_RUST_VERSION:-1.95}"
    if [ ! -x "$HOME/.cargo/bin/rustup" ]; then
        echo "[provision] installing rustup (toolchain ${RUST_VERSION})"
        curl -sSf https://sh.rustup.rs | sh -s -- -y --no-modify-path \
            --profile minimal --default-toolchain "${RUST_VERSION}"
    else
        "$HOME/.cargo/bin/rustup" toolchain install --profile minimal "${RUST_VERSION}"
        "$HOME/.cargo/bin/rustup" default "${RUST_VERSION}"
    fi
fi

echo "[provision] done. versions:"
printf '  qemu:  %s\n' "$(qemu-system-x86_64 --version | head -1)"
printf '  gcc:   %s\n' "$(gcc --version | head -1)"
[ "${DESKTOP}" = 1 ] && printf '  rustc: %s\n' "$("$HOME/.cargo/bin/rustc" --version)"
if [ -w /dev/kvm ]; then
    echo "  /dev/kvm is writable by $(id -un) — KVM OK"
else
    echo "  WARNING: /dev/kvm is not writable by $(id -un) — add yourself to the kvm group, or QEMU falls back to TCG (slow)"
fi
