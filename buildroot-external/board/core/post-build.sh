#!/bin/sh
# PengyOS Core — post-build (runs after the rootfs is assembled).
#
# The CORE LAYER's script: every edition runs it first (configs/layers/ stacks
# the post-build scripts: core, then server, then desktop), so it holds what is
# common to all of them — SSH key injection, no baked host keys, provenance,
# the SBOM, the "not installed" tool stubs. The Core-specific bits (hostname,
# os-release, GRUB menu) are overwritten by a later edition's script.
# There is no busybox /etc/inittab under systemd — the serial getty (ttyS0) is
# started by systemd-getty-generator from console=ttyS0 (grub.cfg adds it only
# when a real UART exists), and its autologin drop-in comes from the rootfs
# overlay (etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf).
set -e

TARGET_DIR="$1"

# --- The kernel config must hold what we asked for -----------------------------
# Kconfig drops a fragment line whose dependencies are unmet without a word, and
# anything we rely on that only x86_64_defconfig provides can vanish on a kernel
# bump (board/*/kernel-required.txt). Fail the build instead of shipping it.
KCONFIG="$(find "${BUILD_DIR}" -maxdepth 2 -path '*/linux-[0-9]*/.config' 2>/dev/null | head -1)"
if [ -n "${KCONFIG}" ] && [ -n "${BR2_CONFIG:-}" ]; then
    python3 "$(dirname "$0")/../../../scripts/check-kernel-fragments.py" \
        --built "${KCONFIG}" --br-config "${BR2_CONFIG}" || {
        echo "post-build: the kernel config lost options listed above" >&2
        exit 1
    }
fi

# Hostname (belt-and-braces alongside BR2_TARGET_GENERIC_HOSTNAME).
echo "pengyos-core" > "${TARGET_DIR}/etc/hostname"

# --- Brand /etc/os-release as PengyOS Core ------------------------------------
# Buildroot ships a generic "Buildroot <version>" os-release; board/common's
# writer replaces it (and the /usr/lib copy) from the repository's VERSION.
sh "$(dirname "$0")/../common/os-release.sh" "${TARGET_DIR}" Core core

# Until 26.10.32 BR2_TARGET_GENERIC_GETTY_PORT was "ttyS0", and Buildroot wrote
# DefaultInstance=ttyS0 into this drop-in, so preset-all enabled a ttyS0 getty on
# every machine. Buildroot only ever writes the file and never removes it, so a
# build tree from before then still carries it. The generator starts the serial
# getty where there is one (see core.config).
rm -f "${TARGET_DIR}/usr/lib/systemd/system/serial-getty@.service.d/buildroot-console.conf"

# Keep a copy of the build identity for provenance/debugging.
if [ -f "${BINARIES_DIR}/buildroot.config" ]; then
    mkdir -p "${TARGET_DIR}/etc/pengyos"
    cp -f "${BINARIES_DIR}/buildroot.config" "${TARGET_DIR}/etc/pengyos/buildroot.config" 2>/dev/null || true
fi

# Bootable image: install our GRUB menu once grub2 has staged efi-part/ in BINARIES_DIR
# (same pattern as Buildroot's board/pc). Requires
# BR2_TARGET_GRUB2_X86_64_EFI.
BOARD_DIR=$(dirname "$0")
if [ -d "${BINARIES_DIR}/efi-part/" ]; then
    cp -f "${BOARD_DIR}/grub-efi.cfg" "${BINARIES_DIR}/efi-part/EFI/BOOT/grub.cfg"
    cp -f "${BOARD_DIR}/grub-pengyos.pf2" "${BINARIES_DIR}/efi-part/EFI/BOOT/grub-pengyos.pf2"
fi

# --- Optional SSH authorized_keys injection (build-time) ---------------------
# Inject one or more SSH public keys into root's authorized_keys so the built
# image is reachable over SSH by key. Opt-in: nothing is baked in unless asked.
# Provide keys either way:
#   PENGYOS_AUTHORIZED_KEYS_FILE=/path/to/id_ed25519.pub   (file of keys)
#   PENGYOS_AUTHORIZED_KEYS="ssh-ed25519 AAAA... you@host" (inline text)
# Example:
#   PENGYOS_AUTHORIZED_KEYS_FILE=$HOME/.ssh/id_ed25519.pub \
#     PYTHONPATH=tools/pengyos-build python3 -m pengyos_build build pengyos-core
KEY_TEXT=""
if [ -n "${PENGYOS_AUTHORIZED_KEYS_FILE:-}" ]; then
    if [ -r "${PENGYOS_AUTHORIZED_KEYS_FILE}" ]; then
        KEY_TEXT=$(cat "${PENGYOS_AUTHORIZED_KEYS_FILE}")
    else
        echo "post-build: PENGYOS_AUTHORIZED_KEYS_FILE=${PENGYOS_AUTHORIZED_KEYS_FILE} is not readable" >&2
    fi
elif [ -n "${PENGYOS_AUTHORIZED_KEYS:-}" ]; then
    KEY_TEXT="${PENGYOS_AUTHORIZED_KEYS}"
fi
# Keep only lines that actually look like public keys (drop blanks/comments).
if [ -n "${KEY_TEXT}" ]; then
    KEY_TEXT=$(printf '%s\n' "${KEY_TEXT}" | grep -E '^(ssh-(rsa|ed25519|dss)|ecdsa-sha2-|sk-)' || true)
fi
if [ -n "${KEY_TEXT}" ]; then
    KEYDIR="${TARGET_DIR}/root/.ssh"
    mkdir -p "${KEYDIR}"
    chmod 700 "${KEYDIR}"
    printf '%s\n' "${KEY_TEXT}" > "${KEYDIR}/authorized_keys"
    chmod 600 "${KEYDIR}/authorized_keys"
    echo "post-build: injected $(printf '%s\n' "${KEY_TEXT}" | wc -l | tr -d ' ') SSH public key(s) into /root/.ssh/authorized_keys"
else
    # Buildroot's target/ persists between builds: drop a key an earlier build
    # injected, or every later image (public downloads included) carries it.
    rm -f "${TARGET_DIR}/root/.ssh/authorized_keys"
    echo "post-build: no SSH keys injected (set PENGYOS_AUTHORIZED_KEYS_FILE to enable SSH login)"
fi

# --- Ship the SBOM inside the image (SPEC provenance / US-CORE-6) ------------
# `pengyos-build sbom <target> --embed` writes <target>.spdx.json (+ .cdx.json,
# .sig) into $(BINARIES_DIR) and re-runs the build so this step bakes them into
# the rootfs. Only an SBOM newer than this build's .config can describe it: a
# plain build after a reconfigure would otherwise ship the last one generated
# (images shipped a weeks-old SBOM that way). Buildroot never deletes target
# files, so clear the previously embedded set first.
rm -f "${TARGET_DIR}"/etc/pengyos/*.spdx.json "${TARGET_DIR}"/etc/pengyos/*.spdx.json.sig \
      "${TARGET_DIR}"/etc/pengyos/*.cdx.json
if [ -d "${BINARIES_DIR}" ]; then
    sbom_installed=0
    for f in "${BINARIES_DIR}"/*.spdx.json "${BINARIES_DIR}"/*.cdx.json "${BINARIES_DIR}"/*.spdx.json.sig; do
        [ -e "$f" ] || continue
        if [ -n "${BR2_CONFIG:-}" ] && [ "${BR2_CONFIG}" -nt "$f" ]; then
            echo "post-build: NOT embedding $(basename "$f"): older than ${BR2_CONFIG} (run pengyos-build sbom --embed)" >&2
            continue
        fi
        mkdir -p "${TARGET_DIR}/etc/pengyos"
        cp -f "$f" "${TARGET_DIR}/etc/pengyos/"
        sbom_installed=1
    done
    # NOTE: guard (not `[ ... ] && echo`) — this script runs under `set -e`, and a
    # bare `[ 0 = 1 ] && echo` returns 1, which would abort target-finalize when no
    # SBOM is staged in BINARIES_DIR (the Server's first-build failure, 2026-09-19).
    if [ "${sbom_installed}" = 1 ]; then
        echo "post-build: embedded SBOM from ${BINARIES_DIR} into /etc/pengyos/"
    fi
fi

# --- SSH host keys: never in the image --------------------------------------
# Every machine generates its own on first boot. Keys baked here were shared by
# every install of the image. Remove any left in the target by earlier builds
# (Buildroot never deletes files from TARGET_DIR).
rm -f "${TARGET_DIR}"/etc/ssh/ssh_host_*
echo "post-build: no ssh host keys in the image (generated per machine on first boot)"

# --- "Not installed" stubs for tools this image lacks --------------------------
# A stub prints what to use instead (for an agent or a human) and exits 127.
# Only for tools the image really lacks: an edition that ships the real tool
# (Desktop: sgdisk, wipefs, …) must not have it shadowed — which is why these
# are not in the rootfs overlay (overlays land after the packages).
for stub in "${BOARD_DIR}"/tool-stubs/*; do
    name=$(basename "$stub")
    have=
    for d in bin sbin usr/bin usr/sbin; do
        p="${TARGET_DIR}/$d/$name"
        # A real tool: exists and is not an earlier build's copy of this stub.
        if [ -e "$p" ] && ! cmp -s "$p" "$stub"; then have=1; fi
    done
    if [ -z "$have" ]; then
        install -D -m 0755 "$stub" "${TARGET_DIR}/usr/bin/$name"
    elif cmp -s "${TARGET_DIR}/usr/bin/$name" "$stub" 2>/dev/null; then
        rm -f "${TARGET_DIR}/usr/bin/$name"
    fi
done

# ensurepip: Buildroot configures Python --without-ensurepip, which breaks
# `python3 -m venv` ("ensurepip is not available") — the everyday venv
# (US-DEV-1). It is pure Python plus CPython's bundled pip wheel; ship
# the one from the same Python source.
PYSRC="$(find "${BUILD_DIR}" -maxdepth 1 -type d -name 'python3-3.*' 2>/dev/null | head -1)"
PYLIB="$(ls -d "${TARGET_DIR}"/usr/lib/python3.* 2>/dev/null | head -1)"
if [ -d "${PYSRC}/Lib/ensurepip" ] && [ -n "${PYLIB}" ]; then
    rm -rf "${PYLIB}/ensurepip"
    cp -a "${PYSRC}/Lib/ensurepip" "${PYLIB}/ensurepip"
    rm -rf "${PYLIB}/ensurepip/__pycache__"
else
    echo "post-build: no ensurepip source — python3 -m venv will not bootstrap pip"
fi

# --- /bin/sh: bash, not BusyBox's ash ------------------------------------------
# `sh -c '...'` is the form an agent writes, and it writes bash-isms into it:
# `[[ ... ]]`, arrays, `shopt`, `local -a`, process substitution, `**` globbing,
# `${x,,}`. BusyBox ash accepts a surprising amount of that and silently
# misbehaves on the rest, so the same script works in a shell session and fails
# under `sh`. Debian puts dash here and Fedora puts bash; we put bash (it is
# already the root shell, and the Server's comfort profile assumes bash syntax).
# BusyBox keeps its ash applet as `ash` for anyone who wants the small one.
if [ -e "${TARGET_DIR}/usr/bin/bash" ]; then
    ln -sf bash "${TARGET_DIR}/usr/bin/sh"
    echo "post-build: /bin/sh -> bash (so sh -c gets bash semantics)"
fi

# --- Build-host paths in systemd units ----------------------------------------
# dbus's user dbus.socket (ExecStartPost=... systemctl --user set-environment:
# Server, Desktop) and systemd-ask-password-plymouth.service (Desktop) carried
# ${HOST_DIR}/bin/... paths from the build machine (found on behoweather,
# 2026-09-30). Point them at the target's tools. Core's script runs first in
# every edition, but later packages' units are already in the target by then.
# Unit files only (systemd's binaries there embed build paths in debug strings;
# never sed a binary).
UNIT_GLOBS="--include=*.service --include=*.socket --include=*.timer --include=*.path --include=*.mount --include=*.target"
grep -rlF $UNIT_GLOBS "${HOST_DIR}/bin/" "${TARGET_DIR}/usr/lib/systemd" 2>/dev/null | while IFS= read -r f; do
    sed -i "s|${HOST_DIR}/bin/|/usr/bin/|g" "$f"
    echo "post-build: fixed build-host path in ${f#${TARGET_DIR}}"
done
if grep -rqF $UNIT_GLOBS "${HOST_DIR}" "${TARGET_DIR}/usr/lib/systemd"; then
    echo "post-build: build-host paths remain in systemd units:" >&2
    grep -rlF $UNIT_GLOBS "${HOST_DIR}" "${TARGET_DIR}/usr/lib/systemd" >&2
    exit 1
fi
# udev rules too: lvm2's 69-dm-lvm.rules ran ${HOST_DIR}/bin/systemd-run until
# 2026-10-03 (fixed at the source, external.mk). Fail rather than ship another.
if grep -rqF --include=*.rules "${HOST_DIR}" "${TARGET_DIR}/usr/lib/udev" "${TARGET_DIR}/etc/udev" 2>/dev/null; then
    echo "post-build: build-host paths in udev rules:" >&2
    grep -rlF --include=*.rules "${HOST_DIR}" "${TARGET_DIR}/usr/lib/udev" "${TARGET_DIR}/etc/udev" >&2
    exit 1
fi

# --- /etc/shells: both spellings of each shell (merged /usr) -------------------
# Packages register /bin/<shell>; the installer gives users /usr/bin/zsh (the
# same file, /bin -> usr/bin). pkexec, chsh and friends compare the string:
# "The value for the SHELL variable was not found in the /etc/shells file"
# (pkexec from the desktop, 2026-09-30). List /usr/bin/ twins too, as distros
# with a merged /usr do.
if [ -f "${TARGET_DIR}/etc/shells" ]; then
    for s in $(grep '^/bin/' "${TARGET_DIR}/etc/shells"); do
        twin="/usr${s}"
        grep -qx "$twin" "${TARGET_DIR}/etc/shells" || echo "$twin" >> "${TARGET_DIR}/etc/shells"
    done
fi

# --- /usr/share/zoneinfo: real directories, as distros ship it ------------------
# Buildroot's tzdata keeps the zones in posix/ (and right/) and makes the top
# level symlinks (America -> posix/America). Code that walks the database
# without following directory links -- jiff, used by COSMIC's clock applet and
# lock screen -- then finds no "America/Toronto": a timezone change reached the
# panel only at the next minute tick, and the lock screen logged "Failed to
# parse timezone" every minute (behoweather, 2026-09-30). Move the posix zones
# up and make posix a link to "." (Debian's layout). right/ stays as it is.
ZI="${TARGET_DIR}/usr/share/zoneinfo"
if [ -d "${ZI}/posix" ] && [ ! -L "${ZI}/posix" ]; then
    for e in "${ZI}/posix"/*; do
        n="${e##*/}"
        if [ -L "${ZI}/${n}" ] || [ ! -e "${ZI}/${n}" ]; then
            rm -f "${ZI}/${n}"
            mv "$e" "${ZI}/${n}"
        fi
    done
    rm -rf "${ZI}/posix"
    ln -s . "${ZI}/posix"
    echo "post-build: zoneinfo flattened (posix -> .)"
fi
[ -f "${ZI}/America/Toronto" ] && [ ! -L "${ZI}/America" ]

# --- Last: every shared library the image needs is in it ------------------------
# (the edition's later post-build runs this again over the finished tree)
python3 "$(dirname "$0")/../common/check-needed-libs.py" "${TARGET_DIR}"
