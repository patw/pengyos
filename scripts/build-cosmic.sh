#!/usr/bin/env bash
# Build the host-side half of the PengyOS Desktop: our libcosmic apps (the graphical
# installer, PengyOS Updates and the other tools/ apps). board/desktop/post-build.sh
# copies them into the image; run this before `pengyos-build build pengyos-desktop`.
#
#   scripts/build-cosmic.sh [APP ...]
#   scripts/build-cosmic.sh --relock COMPONENT ...
#   scripts/build-cosmic.sh --fetch-only [COMPONENT ...]
#
# The COSMIC components themselves (scripts/cosmic-components.tsv) are no longer
# built here: the Buildroot package buildroot-external/package/cosmic builds them
# from the same TSV, pin, locks and patches (since 2026-10-28).
#
# What stays here is resolving dependencies. Every component builds with `--locked`
# against the lock PengyOS owns, scripts/cosmic-locks/<component>.Cargo.lock; a
# crate's build script and proc-macros run on the build host, so which crates those
# are is a reviewed commit, not whatever crates.io serves on the day of the build
# (see scripts/cosmic-lock-check.py for what that cost). --relock checks out the
# named components into $PENGYOS_COSMIC_SRC (default ~/cosmic-src), re-resolves
# them after the pin, writes their locks into the repo, prints what changed, and
# builds nothing; review the diff, commit it, and run scripts/cosmic-recipe.py.
# --fetch-only only checks the components out (to read or patch them).
#
# The apps' target dir is $PENGYOS_COSMIC_BUILD/install-gui (default
# ~/cosmic-build/install-gui), which post-build reads.
#
# Host requirements: scripts/provision-build-host.sh --desktop (the -dev packages
# the apps link against, and rustup's toolchain).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LIST="${REPO}/scripts/cosmic-components.tsv"
PATCHES="${REPO}/buildroot-external/board/desktop/cosmic-patches"
LIBCOSMIC_PIN_FILE="${REPO}/scripts/cosmic-libcosmic.pin"
PIN_TOOL="${REPO}/scripts/cosmic-libcosmic-pin.py"
LOCKS="${REPO}/scripts/cosmic-locks"
LOCK_TOOL="${REPO}/scripts/cosmic-lock-check.py"
SRC="${PENGYOS_COSMIC_SRC:-$HOME/cosmic-src}"
BUILD="${PENGYOS_COSMIC_BUILD:-$HOME/cosmic-build}"
export PATH="$HOME/.cargo/bin:$PATH"   # rustup's cargo before the distro's
unset LD_LIBRARY_PATH                  # a trailing ':' breaks some build scripts

FETCH_ONLY=0
RELOCK=0
if [ "${1:-}" = "--fetch-only" ]; then FETCH_ONLY=1; shift; fi
if [ "${1:-}" = "--relock" ]; then RELOCK=1; shift; fi
ONLY=("$@")
if [ "${RELOCK}" = 1 ] && [ "${#ONLY[@]}" -eq 0 ]; then
    echo "[cosmic] --relock needs the components to re-resolve, by name" >&2
    exit 2
fi

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

# Re-resolve one component after the pin and write its lock into the repo. Cargo
# resolves the minimum the pin needs first (`cargo fetch` runs no build script);
# only if the pin still does not take is the whole graph re-resolved - the
# fallback the normal build used to take silently. Either way the result is shown
# against the previous lock (or upstream's) and nothing is compiled.
relock() {   # name
    local dir="${SRC}/$1" lock="${LOCKS}/$1.Cargo.lock"
    [ -e "${dir}/Cargo.toml" ] || { echo "[cosmic] $1: not a cargo component" >&2; exit 1; }
    if [ -n "${LIBCOSMIC_REV}" ]; then
        if [ ! -e "${dir}/Cargo.lock" ]; then
            (cd "${dir}" && cargo generate-lockfile --quiet)
        fi
        python3 "${PIN_TOOL}" "${dir}" "${LIBCOSMIC_REV}"
    fi
    (cd "${dir}" && cargo fetch --quiet)
    if [ -n "${LIBCOSMIC_REV}" ] && ! python3 "${PIN_TOOL}" --verify "${dir}" "${LIBCOSMIC_REV}"; then
        echo "[cosmic] $1: pin unused by the graph, re-resolving its whole lock"
        (cd "${dir}" && cargo generate-lockfile --quiet && cargo fetch --quiet)
        python3 "${PIN_TOOL}" --verify "${dir}" "${LIBCOSMIC_REV}" || {
            echo "[cosmic] $1: the coherence pin did not take effect" >&2; exit 1; }
    fi
    local ref="git:${dir}"
    [ -e "${lock}" ] && ref="${lock}"
    python3 "${LOCK_TOOL}" "${dir}/Cargo.lock" "${ref}" || true
    mkdir -p "${LOCKS}"
    cp -f "${dir}/Cargo.lock" "${lock}"
    echo "[cosmic] $1: wrote ${lock#"${REPO}/"} - review the diff above and commit it"
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
if [ "${FETCH_ONLY}" = 1 ] || [ "${RELOCK}" = 1 ]; then
    while IFS=$'\t' read -r name url commit args; do
        case "${name}" in ''|'#'*) continue ;; esac
        wanted "${name}" || continue
        checkout "${name}" "${url}" "${commit}"
        if [ "${RELOCK}" = 1 ] && [ "${args}" != "-" ]; then
            relock "${name}"
        fi
    done < "${LIST}"
fi

# Our libcosmic apps share one target dir so they share the libcosmic build.
if [ "${FETCH_ONLY}" = 0 ] && [ "${RELOCK}" = 0 ]; then
    for app in pengyos-install-gui pengyos-updates pengyos-disks pengyos-usage pengyos-gamepads pengyos-printers pengyos-firmware pengyos-sharing pengyos-remote-desktop pengy-games; do
        wanted "${app}" || continue
        echo "[cosmic] cargo build --release -p ${app} --features cosmic --locked"
        (cd "${REPO}/tools/${app}" && \
            CARGO_TARGET_DIR="${BUILD}/install-gui" cargo build --release -p "${app}" --features cosmic --locked)
        built=$((built + 1))
    done
fi

# Not a libcosmic app, but host-built the same way (it links the host's
# libpipewire, as cosmic-settings does) and into the same target dir.
if [ "${FETCH_ONLY}" = 0 ] && [ "${RELOCK}" = 0 ] && wanted pengyos-rdp-server; then
    echo "[cosmic] cargo build --release -p pengyos-rdp-server --locked"
    (cd "${REPO}/tools/pengyos-rdp-server" && \
        CARGO_TARGET_DIR="${BUILD}/install-gui" cargo build --release -p pengyos-rdp-server --locked)
    built=$((built + 1))
fi

echo "[cosmic] done: ${built} cargo builds into ${BUILD}"
