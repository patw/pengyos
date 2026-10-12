#!/bin/sh
# PengyOS Server — post-build (runs after the rootfs is assembled).
#
# Runs AFTER Core's post-build (configs/layers/ stacks the scripts), which does
# the common work (SSH keys, host keys, provenance, SBOM, tool stubs). This one
# only makes the image the Server: hostname, os-release, GRUB menu.
set -e

TARGET_DIR="$1"

# Hostname (belt-and-braces alongside BR2_TARGET_GENERIC_HOSTNAME).
echo "pengyos-server" > "${TARGET_DIR}/etc/hostname"

# --- Brand /etc/os-release as PengyOS Server ------------------------------------
# Buildroot ships a generic "Buildroot <version>" os-release; board/common's
# writer replaces it (and the /usr/lib copy) from the repository's VERSION.
# The NVIDIA edition shares this board: name it apart (IMAGE_ID is what an
# update feed matches, and an NVIDIA machine must only take NVIDIA images).
# (The Desktop's post-build runs after this one and names its own editions.)
if [ -x "${TARGET_DIR}/usr/bin/nvidia-smi" ]; then
    sh "$(dirname "$0")/../common/os-release.sh" "${TARGET_DIR}" "Server NVIDIA" server-nvidia
else
    sh "$(dirname "$0")/../common/os-release.sh" "${TARGET_DIR}" Server server
fi

# Bootable image: install our GRUB menu once grub2 has staged efi-part/ in
# BINARIES_DIR (same pattern as Core). Requires BR2_TARGET_GRUB2_X86_64_EFI.
BOARD_DIR=$(dirname "$0")
if [ -d "${BINARIES_DIR}/efi-part/" ]; then
    cp -f "${BOARD_DIR}/grub-efi.cfg" "${BINARIES_DIR}/efi-part/EFI/BOOT/grub.cfg"
    cp -f "${BOARD_DIR}/grub-pengyos.pf2" "${BINARIES_DIR}/efi-part/EFI/BOOT/grub-pengyos.pf2"
fi
# The image carries its own GRUB too, so an update can bring a new one to the
# boot partition (shared by both slots): update.d/50-grub-efi copies it over.
if [ -f "${BINARIES_DIR}/efi-part/EFI/BOOT/bootx64.efi" ]; then
    install -Dm0644 "${BINARIES_DIR}/efi-part/EFI/BOOT/bootx64.efi" \
        "${TARGET_DIR}/usr/lib/pengyos/boot/bootx64.efi"
    install -Dm0644 "${BOARD_DIR}/grub-pengyos.pf2" "${TARGET_DIR}/usr/lib/pengyos/boot/grub-pengyos.pf2"
fi

# --- The installer: engine + console installer ---------------------------------
# pengyos-install (engine) and pengyos-install-tui (console installer) are
# stdlib-only Python packages from this repo, copied into the target's
# site-packages with /usr/bin wrappers. The Server stick runs the console
# installer on tty1 (pengyos-console-installer.service); the Desktop adds its
# GUI on top. An installed system has them too, harmlessly: no payload.
# pengyos-update (the updater) is installed the same way; it renders grub.cfg
# with pengyos_install's code, so the two always agree.
PENGYOS_TOOLS="$(cd "$(dirname "$0")/../../../tools" && pwd)"
SITE="$(ls -d "${TARGET_DIR}"/usr/lib/python3.*/site-packages 2>/dev/null | head -1)"
if [ -n "${SITE}" ]; then
    for pkg in pengyos-install/src/pengyos_install pengyos-install-tui/pengyos_install_tui \
               pengyos-update/src/pengyos_update; do
        rm -rf "${SITE}/$(basename "${pkg}")"
        cp -r "${PENGYOS_TOOLS}/${pkg}" "${SITE}/"
        find "${SITE}/$(basename "${pkg}")" -name __pycache__ -prune -exec rm -rf {} +
    done
    for cmd in pengyos-install:pengyos_install pengyos-install-tui:pengyos_install_tui \
               pengyos-update:pengyos_update; do
        printf '#!/bin/sh\nexec /usr/bin/python3 -m %s "$@"\n' "${cmd#*:}" > "${TARGET_DIR}/usr/bin/${cmd%%:*}"
        chmod 755 "${TARGET_DIR}/usr/bin/${cmd%%:*}"
    done
    echo "post-build: installed pengyos-install + pengyos-install-tui + pengyos-update (${SITE#${TARGET_DIR}})"

    # The dev box (pengyos-guest): distrobox guests declared in
    # /usr/share/pengyos/guests.d, created on first `pengyos-guest enter`.
    # The CUDA box only where the NVIDIA driver is (libcuda from nvidia-open).
    rm -rf "${SITE}/pengyos_guest"
    cp -r "${PENGYOS_TOOLS}/pengyos-guest/src/pengyos_guest" "${SITE}/"
    find "${SITE}/pengyos_guest" -name __pycache__ -prune -exec rm -rf {} +
    printf '#!/bin/sh\nexec /usr/bin/python3 -m pengyos_guest "$@"\n' > "${TARGET_DIR}/usr/bin/pengyos-guest"
    chmod 755 "${TARGET_DIR}/usr/bin/pengyos-guest"
    GUESTS_D="${TARGET_DIR}/usr/share/pengyos/guests.d"
    rm -rf "${GUESTS_D}"
    install -Dm0644 "${PENGYOS_TOOLS}/pengyos-guest/guests.d/dev.toml" "${GUESTS_D}/dev.toml"
    if ls "${TARGET_DIR}"/usr/lib/libcuda.so* >/dev/null 2>&1; then
        install -Dm0644 "${PENGYOS_TOOLS}/pengyos-guest/guests.d/cuda.toml" "${GUESTS_D}/cuda.toml"
    fi
    echo "post-build: installed pengyos-guest ($(ls "${GUESTS_D}" | tr '\n' ' '))"
else
    echo "post-build: no python3 in the target — the installer is NOT installed"
fi

# --- Partition & state layout (docs/state-layout.md) --------------------------
# Installed systems boot the root slot READ-ONLY with `pengyos.data=N`; preinit
# (/usr/lib/pengyos/preinit, the kernel's init=) mounts the data partition on
# /data and lays the machine's state over the image. No root line in fstab: the
# kernel's `ro` / `rw` decides (the stick's grub.cfg says `rw`), so
# systemd-remount-fs never flips an installed root to read-write.
mkdir -p "${TARGET_DIR}/data"
printf '# Root is mounted by the kernel (ro on installed systems, rw on the stick);\n# machine state lives on the data partition, see /usr/lib/pengyos/preinit.\n' \
    > "${TARGET_DIR}/etc/fstab"
echo "post-build: /data mount point + fstab without a root entry (state layout)"
# sudoers drop-ins must be root-only readable (the overlay copy makes them 0644).
chmod 0440 "${TARGET_DIR}"/etc/sudoers.d/* 2>/dev/null || true
# /efi: installed systems automount the ESP there (fstab written by the installer)
# for fwupd's UEFI capsules; the root is read-only, so the mount point ships here.
mkdir -p "${TARGET_DIR}/efi"

# --- PAM: no lines for modules the image does not have ---------------------------
# util-linux's /etc/pam.d/login names pam_selinux (no SELinux here) and
# pam_console (Red Hat's, long gone). Both are optional/ignored, so console
# logins worked, but PAM logged "unable to dlopen(pam_selinux.so)" at error
# level on every one (26.10.23 QEMU probe).
if [ -f "${TARGET_DIR}/etc/pam.d/login" ]; then
    sed -i '/pam_selinux\.so/d; /pam_console\.so/d' "${TARGET_DIR}/etc/pam.d/login"
fi

# --- The comfort shell profile (US-SRV-1) --------------------------------------
# tools/pengyos-server-toolset authors the curated bash/zsh profile (starship
# prompt, fzf, zoxide, modern aliases, tmux defaults); the image installs it at
# its canonical system path. The wiring lives in the server overlay:
#   /etc/zshrc + /etc/zsh/zshrc  -> zsh (the user's shell) sources it
#   /etc/profile.d/pengyos-shell.sh -> bash login shells source it
#   /etc/skel/.zshrc (+ .bashrc) -> new accounts source it and own the copy
# Every piece is guarded on the file existing, so a toolset-less tree is fine.
SHELL_DIR="${TARGET_DIR}/etc/pengyos/shell"
if [ -d "${PENGYOS_TOOLS}/pengyos-server-toolset/shell" ]; then
    install -Dm0644 "${PENGYOS_TOOLS}/pengyos-server-toolset/shell/profile"    "${SHELL_DIR}/profile"
    install -Dm0644 "${PENGYOS_TOOLS}/pengyos-server-toolset/shell/fzf.sh"     "${SHELL_DIR}/fzf.sh"
    install -Dm0644 "${PENGYOS_TOOLS}/pengyos-server-toolset/shell/zoxide.sh"  "${SHELL_DIR}/zoxide.sh"
    install -Dm0644 "${PENGYOS_TOOLS}/pengyos-server-toolset/shell/starship.toml" "${SHELL_DIR}/starship.toml"
    echo "post-build: installed the comfort shell profile (/etc/pengyos/shell)"
else
    echo "post-build: no server-toolset shell/ in the tree — profile NOT installed"
fi
# tmux: the toolset's defaults system-wide (a fresh `tmux` on a Server feels
# the same everywhere: truecolor, mouse, sane key-chords).
if [ -f "${PENGYOS_TOOLS}/pengyos-server-toolset/shell/tmux.conf" ]; then
    install -Dm0644 "${PENGYOS_TOOLS}/pengyos-server-toolset/shell/tmux.conf" "${TARGET_DIR}/etc/tmux.conf"
fi

# --- Last: every shared library the image needs is in it ------------------------
# (the edition's later post-build runs this again over the finished tree)
python3 "$(dirname "$0")/../common/check-needed-libs.py" "${TARGET_DIR}"
