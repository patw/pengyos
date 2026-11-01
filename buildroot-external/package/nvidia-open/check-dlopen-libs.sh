#!/bin/sh
# nvidia-open: fail the build when an installed NVIDIA program or library
# names a driver library (to dlopen) that the .run bundle has but we did not
# install. dlopen()ed libraries are not in DT_NEEDED, so check-needed-libs and
# the QEMU boot check (no NVIDIA GPU) never see them: nvidia-persistenced
# without libnvidia-cfg.so.1 failed only on real hardware (26.10.52, behoserver).
#
# usage: check-dlopen-libs.sh BUNDLE_DIR TARGET_DIR
#
# Optional extras we choose not to ship: CUDA debugger (cuda-gdb), PKCS#11 for
# confidential computing, the CUDA tile IR compiler, the container sandbox
# helper; and libcuda's CUDA-OpenGL/EGL interop vendors (libEGL_nvidia,
# libGLX_nvidia), which only the graphics options install: a headless Server
# has no GL. Add a library here only with a reason.
OPTIONAL="libcudadebugger libnvidia-pkcs11 libnvidia-pkcs11-openssl3 libnvidia-tileiras libnvidia-sandboxutils libEGL_nvidia libGLX_nvidia"

bundle="$1"
target="$2"
[ -d "${bundle}" ] && [ -d "${target}" ] || { echo "usage: $0 BUNDLE_DIR TARGET_DIR" >&2; exit 2; }

missing=""
# Scan what this package installed from the bundle (same name in /usr/bin or
# /usr/lib), not other packages: nvidia-ctk lists every library it might mount.
for b in "${bundle}"/*; do
    [ -f "${b}" ] && [ ! -L "${b}" ] || continue
    f="${target}/usr/bin/${b##*/}"
    [ -f "${f}" ] || f="${target}/usr/lib/${b##*/}"
    [ -f "${f}" ] && [ ! -L "${f}" ] || continue
    for name in $(grep -aoE 'lib(nv|cuda|EGL_nv|GLESv[12]_[A-Z_]*nv|GLX_nv)[A-Za-z0-9_-]*\.so(\.[0-9]+)*' "${f}" | sort -u); do
        base="${name%%.so*}"
        case " ${OPTIONAL} " in *" ${base} "*) continue ;; esac
        # Only libraries NVIDIA ships in this bundle; other names are search
        # lists (nvidia-ctk) or libraries from elsewhere.
        ls "${bundle}/${base}.so."* >/dev/null 2>&1 || continue
        ls "${target}/usr/lib/${base}.so"* >/dev/null 2>&1 && continue
        case "${missing}" in *" ${base} "*) continue ;; esac
        missing="${missing} ${base} (named by ${f##*/})"
    done
done

if [ -n "${missing}" ]; then
    echo "nvidia-open: driver libraries dlopened but not installed:${missing}" >&2
    echo "  add each to NVIDIA_OPEN_LIBS, or to OPTIONAL in $0 with a reason" >&2
    exit 1
fi
