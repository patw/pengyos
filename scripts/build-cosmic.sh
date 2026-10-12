#!/usr/bin/env bash
# Build the host-side half of the PengyOS Desktop: the COSMIC components pinned in
# scripts/cosmic-components.tsv, plus our two libcosmic apps (the graphical
# installer and PengyOS Updates). board/desktop/post-build.sh copies the results
# into the image; run this before `pengyos-build build pengyos-desktop`.
#
#   scripts/build-cosmic.sh [--fetch-only] [COMPONENT ...]
#
# Sources go to $PENGYOS_COSMIC_SRC (default ~/cosmic-src), cargo's target dir is
# $PENGYOS_COSMIC_BUILD (default ~/cosmic-build) — the same variables post-build
# reads. Idempotent: an existing checkout is moved to its pinned commit, a patch
# that is already applied is skipped, and cargo only rebuilds what changed.
#
# Host requirements: scripts/provision-build-host.sh --desktop (the -dev packages
# COSMIC links against, and rustup's toolchain — some components need a newer
# rustc than the distribution ships).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LIST="${REPO}/scripts/cosmic-components.tsv"
PATCHES="${REPO}/buildroot-external/board/desktop/cosmic-patches"
LIBCOSMIC_PIN_FILE="${REPO}/scripts/cosmic-libcosmic.pin"
PIN_TOOL="${REPO}/scripts/cosmic-libcosmic-pin.py"
SRC="${PENGYOS_COSMIC_SRC:-$HOME/cosmic-src}"
BUILD="${PENGYOS_COSMIC_BUILD:-$HOME/cosmic-build}"
export PATH="$HOME/.cargo/bin:$PATH"   # rustup's cargo before the distro's
unset LD_LIBRARY_PATH                  # a trailing ':' breaks some build scripts

FETCH_ONLY=0
if [ "${1:-}" = "--fetch-only" ]; then FETCH_ONLY=1; shift; fi
ONLY=("$@")

wanted() {
    [ "${#ONLY[@]}" -eq 0 ] && return 0
    local c; for c in "${ONLY[@]}"; do [ "$c" = "$1" ] && return 0; done
    return 1
}

checkout() {   # name url commit
    local dir="${SRC}/$1"
    if [ ! -d "${dir}/.git" ]; then
        echo "[cosmic] clone $1"
        git clone --quiet "$2" "${dir}"
    fi
    if [ "$(git -C "${dir}" rev-parse HEAD)" != "$3" ]; then
        git -C "${dir}" cat-file -e "$3^{commit}" 2>/dev/null || git -C "${dir}" fetch --quiet origin
        git -C "${dir}" checkout --quiet --detach "$3"
        echo "[cosmic] $1 at ${3:0:12}"
    fi
    # Always drop local changes, not only when the commit moved. The COSMIC
    # patches and the libcosmic coherence pin are both applied to the working
    # tree, and re-applying them onto a tree that already has them duplicates a
    # [patch."https://github.com/pop-os/libcosmic"] table, which is a TOML error.
    git -C "${dir}" checkout --quiet -- .
    local p
    for p in "${PATCHES}/$1"/*.patch; do
        [ -e "$p" ] || continue
        if git -C "${dir}" apply --reverse --check "$p" 2>/dev/null; then
            continue                        # already applied
        fi
        echo "[cosmic] $1: apply $(basename "$p")"
        git -C "${dir}" apply "$p"
    done
}

mkdir -p "${SRC}" "${BUILD}"
# One libcosmic for the whole set. Each component's own Cargo.lock had frozen its
# own libcosmic commit (14 distinct ones as measured 2026-10-05); the pin file
# names the single rev and the helper appends the cargo patch. Set
# PENGYOS_LIBCOSMIC_PIN=0 to build without it (debugging only).
LIBCOSMIC_REV="$(grep -vE '^[[:space:]]*#' "${LIBCOSMIC_PIN_FILE}" \
    | grep -oE '\b[0-9a-f]{7,40}\b' | head -1)"
[ "${PENGYOS_LIBCOSMIC_PIN:-1}" = 0 ] && LIBCOSMIC_REV=""
if [ -n "${LIBCOSMIC_REV}" ]; then
    echo "[cosmic] libcosmic coherence pin: ${LIBCOSMIC_REV:0:12}"
fi
built=0
while IFS=$'\t' read -r name url commit args; do
    case "${name}" in ''|'#'*) continue ;; esac
    wanted "${name}" || continue
    checkout "${name}" "${url}" "${commit}"
    [ "${FETCH_ONLY}" = 1 ] && continue
    [ "${args}" = "-" ] && continue
    if [ -n "${LIBCOSMIC_REV}" ] && [ -e "${SRC}/${name}/Cargo.toml" ]; then
        # The helper reads the component's Cargo.lock to learn which crates it
        # pulls out of libcosmic. Six components ship no lock upstream, so make
        # one first - which is what the build itself would do anyway.
        if [ ! -e "${SRC}/${name}/Cargo.lock" ]; then
            (cd "${SRC}/${name}" && CARGO_TARGET_DIR="${BUILD}" cargo generate-lockfile --quiet)
        fi
        python3 "${PIN_TOOL}" "${SRC}/${name}" "${LIBCOSMIC_REV}"
    fi
    echo "[cosmic] cargo ${args}  (${name})"
    # Run through a shell so the column can be a `&&`-joined command list — the
    # cosmic-files row needs the app and the applet built as two separate cargo
    # invocations (see the TSV). -e: a failed first command stops the component.
    (cd "${SRC}/${name}" && CARGO_TARGET_DIR="${BUILD}" sh -ec "cargo ${args}")
    if [ -n "${LIBCOSMIC_REV}" ] && [ -e "${SRC}/${name}/Cargo.toml" ]; then
        # A cargo patch only lands if the pinned rev's crate VERSIONS satisfy the
        # graph. A stale component can get "patch was not used in the crate graph"
        # and silently keep its old libcosmic - cosmic-idle wanted iced_core
        # 0.14.0-dev from 2025-10-13 while the pin provides 0.14.0. Build first
        # (cargo re-resolves exactly what the patch affects); re-resolve the whole
        # graph only if the pin still did not take.
        if ! python3 "${PIN_TOOL}" --verify "${SRC}/${name}" "${LIBCOSMIC_REV}"; then
            echo "[cosmic] ${name}: pin unused by the graph, re-resolving its lock"
            (cd "${SRC}/${name}" && CARGO_TARGET_DIR="${BUILD}" cargo generate-lockfile --quiet)
            (cd "${SRC}/${name}" && CARGO_TARGET_DIR="${BUILD}" sh -ec "cargo ${args}")
            python3 "${PIN_TOOL}" --verify "${SRC}/${name}" "${LIBCOSMIC_REV}" || {
                echo "[cosmic] ${name}: the coherence pin did not take effect"; exit 1; }
        fi
    fi
    built=$((built + 1))
done < "${LIST}"

# --- Guard: the cosmic-files APP must not carry the desktop-applet code -------
# cosmic-files and cosmic-files-applet are one cargo workspace, and the applet
# depends on the app crate with `desktop-applet`. Building them in ONE cargo
# invocation lets cargo feature-unify `desktop-applet` onto the cosmic-files
# *app* binary, which then also creates the applet's full-screen Bottom-layer
# "desktop" surface — the file manager draws a second, interactive copy of the
# file view on the desktop wallpaper (reproduced 2026-10-06 in QEMU; see
# CHANGELOG). cosmic-components.tsv builds them as two invocations; fail loudly
# if a future edit reintroduces the single workspace build.
if [ "${FETCH_ONLY}" = 0 ] && wanted cosmic-files && [ -x "${BUILD}/release/cosmic-files" ]; then
    if grep -aq "already had surface ID" "${BUILD}/release/cosmic-files"; then
        echo "[cosmic] ERROR: cosmic-files was built WITH the desktop-applet feature" >&2
        echo "[cosmic]        it would draw a second copy of the file view on the desktop." >&2
        echo "[cosmic]        Build cosmic-files and cosmic-files-applet as separate cargo" >&2
        echo "[cosmic]        invocations (see the cosmic-files row in cosmic-components.tsv)." >&2
        exit 1
    fi
    if [ -x "${BUILD}/release/cosmic-files-applet" ] &&
       ! grep -aq "already had surface ID" "${BUILD}/release/cosmic-files-applet"; then
        echo "[cosmic] ERROR: cosmic-files-applet is missing the desktop-applet code" >&2
        exit 1
    fi
fi

# Our libcosmic apps share one target dir so they share the libcosmic build.
if [ "${FETCH_ONLY}" = 0 ]; then
    for app in pengyos-install-gui pengyos-updates pengyos-disks; do
        wanted "${app}" || continue
        echo "[cosmic] cargo build --release -p ${app} --features cosmic"
        (cd "${REPO}/tools/${app}" && \
            CARGO_TARGET_DIR="${BUILD}/install-gui" cargo build --release -p "${app}" --features cosmic)
        built=$((built + 1))
    done
fi

echo "[cosmic] done: ${built} cargo builds into ${BUILD}"
