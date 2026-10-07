#!/usr/bin/env bash
# Rebuild, check and (optionally) publish PengyOS images in one go.
#
#   scripts/release.sh [options] [TARGET ...]        default: all five images
#
#   --publish        sign and publish into the local site dir: update-publish for
#                    the A/B editions (Desktop, Desktop NVIDIA, Server, Server
#                    NVIDIA) and one
#                    download-publish for every target built. Needs
#                    $PENGYOS_RELEASE_KEY.
#   --push           rsync the site dir to $PENGYOS_SITE_REMOTE (host:path/).
#                    Implies --publish.
#   --channel C      update channel for --publish (default: testing)
#   --notes FILE     release notes for update-publish
#   --skip-cosmic    don't rebuild COSMIC before a Desktop target
#   --no-boot-check  skip the QEMU checks (automatic, with a warning, on a host
#                    without KVM: software emulation is too slow for them)
#   --allow-dirty    build from a tree with uncommitted changes (BUILD_ID gets
#                    "-dirty"; refused with --publish)
#
# Steps per target: configure -> build -> SBOM (generated and embedded, so the
# image carries a current one) -> boot check under QEMU (the smoke matrix for
# Core and Server; for the Desktops a login check plus scripts/qemu-desktop-check.py,
# which proves the COSMIC session runs). Nothing is signed or pushed
# unless asked: signing a release and putting it in front of machines is a
# human decision (CLAUDE.md, "Security boundary").
#
# Bump VERSION and commit before running: images take IMAGE_VERSION from it and
# BUILD_ID from the commit.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGETS_FILE="${REPO}/targets.toml"
BUILD_ROOT="${PENGYOS_BUILD_ROOT:-$HOME/build/pengyos}"
SITE="${PENGYOS_SITE:-${BUILD_ROOT}/site}"
CHANNEL=testing NOTES="" PUBLISH=0 PUSH=0 SKIP_COSMIC=0 ALLOW_DIRTY=0 BOOT_CHECK=1
TARGETS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --publish) PUBLISH=1 ;;
        --push) PUBLISH=1; PUSH=1 ;;
        --channel) CHANNEL="$2"; shift ;;
        --notes) NOTES="$2"; shift ;;
        --skip-cosmic) SKIP_COSMIC=1 ;;
        --no-boot-check) BOOT_CHECK=0 ;;
        --allow-dirty) ALLOW_DIRTY=1 ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        -*) echo "release: unknown option $1" >&2; exit 2 ;;
        *) TARGETS+=("$1") ;;
    esac
    shift
done
[ "${#TARGETS[@]}" -gt 0 ] || TARGETS=(pengyos-core pengyos-server pengyos-server-nvidia pengyos-desktop pengyos-desktop-nvidia)

say() { printf '\n[release] %s\n' "$*"; }
die() { echo "[release] $*" >&2; exit 1; }
unset LD_LIBRARY_PATH   # a trailing ':' breaks Buildroot
# Timestamps baked into the images (kernel banner, archives) come from the
# commit, not the clock, so two builds of one commit agree (fully only with
# BR2_REPRODUCIBLE; Buildroot's own default is its release date).
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "${REPO}" log -1 --format=%ct)}"

VERSION="$(tr -d ' \n' < "${REPO}/VERSION")"
if [ -n "$(git -C "${REPO}" status --porcelain)" ]; then
    [ "${PUBLISH}" = 1 ] && die "uncommitted changes: commit before publishing (BUILD_ID would be -dirty)"
    [ "${ALLOW_DIRTY}" = 1 ] || die "uncommitted changes (use --allow-dirty for a test build)"
fi
if [ "${PUBLISH}" = 1 ]; then
    [ -r "${PENGYOS_RELEASE_KEY:-}" ] || die "--publish needs PENGYOS_RELEASE_KEY (the pengyos-sign private key)"
    [ -z "${PENGYOS_AUTHORIZED_KEYS_FILE:-}" ] || die "PENGYOS_AUTHORIZED_KEYS_FILE is set: published images must not carry a test key"
fi
[ "${PUSH}" = 0 ] || [ -n "${PENGYOS_SITE_REMOTE:-}" ] || die "--push needs PENGYOS_SITE_REMOTE (host:path/)"
if [ "${BOOT_CHECK}" = 1 ] && [ ! -w /dev/kvm ]; then
    echo "[release] WARNING: no writable /dev/kvm — skipping the QEMU boot checks" >&2
    BOOT_CHECK=0
fi
[ "${PUBLISH}" = 1 ] && [ "${BOOT_CHECK}" = 0 ] && die "refusing to publish images that were not boot-checked"

build() { (cd "${REPO}/tools/pengyos-build" && python3 -m pengyos_build "$@" --targets "${TARGETS_FILE}"); }
verify() { (cd "${REPO}/tools/pengyos-qemu-verify" && python3 -m pengyos_qemu_verify "$@"); }
out() { echo "${BUILD_ROOT}/output/$1"; }

say "PengyOS ${VERSION} ($(git -C "${REPO}" describe --always --dirty)) — ${TARGETS[*]}"

case " ${TARGETS[*]} " in
    *" pengyos-desktop"*)
        if [ "${SKIP_COSMIC}" = 0 ]; then
            say "COSMIC (scripts/build-cosmic.sh)"
            "${REPO}/scripts/build-cosmic.sh"
        fi ;;
esac

for t in "${TARGETS[@]}"; do
    say "${t}: configure + build"
    build configure "${t}"
    build build "${t}"
    say "${t}: SBOM (generate + embed)"
    build sbom "${t}" --embed

    got="$(sed -n 's/^IMAGE_VERSION="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$(out "${t}")/target/usr/lib/os-release")"
    [ "${got}" = "${VERSION}" ] || die "${t}: image says IMAGE_VERSION=${got}, VERSION is ${VERSION}"

    [ "${BOOT_CHECK}" = 1 ] || continue
    say "${t}: boot check"
    case "${t}" in
        pengyos-core) verify smoke --matrix smoke/core.toml --image "$(out "${t}")/images/pengyos-core-x86_64.img" ;;
        # Server NVIDIA: the Server matrix (a VM has no NVIDIA GPU, so the driver
        # stays unloaded; CUDA is checked on hardware).
        pengyos-server|pengyos-server-nvidia)
           verify smoke --matrix smoke/server.toml --image "$(out "${t}")/images/pengyos-server-x86_64.img" ;;
        *) verify boot "$(out "${t}")/images/pengyos-desktop-x86_64.img" --uefi --ram 2048 --timeout 240
           # A login prompt is not a desktop: boot the installer stick and check the
           # COSMIC session itself (26.10.3 passed the line above with a dead
           # compositor and refused console logins).
           verify desktop "$(out "${t}")/images/pengyos-desktop-x86_64-installer.img" \
               --out "$(out "${t}")/images/qemu-desktop-check" ;;
    esac
done

if [ "${PUBLISH}" = 1 ]; then
    rel() { (cd "${REPO}/tools/pengyos-release" && python3 -m pengyos_release "$@"); }
    notes=(); [ -n "${NOTES}" ] && notes=(--notes "@${NOTES}")
    dl=()
    for t in "${TARGETS[@]}"; do
        dl+=(--build "$(out "${t}")")
        [ "${t}" = pengyos-core ] && continue        # Core has no A/B layout yet
        say "${t}: update-publish (${CHANNEL})"
        rel update-publish --build "$(out "${t}")" --site "${SITE}" \
            --key "${PENGYOS_RELEASE_KEY}" --channel "${CHANNEL}" "${notes[@]}"
    done
    say "download-publish"
    rel download-publish "${dl[@]}" --site "${SITE}" --key "${PENGYOS_RELEASE_KEY}"
    rel update-feed --site "${SITE}"
fi

if [ "${PUSH}" = 1 ]; then
    say "push ${SITE}/ -> ${PENGYOS_SITE_REMOTE}"
    rsync -a "${SITE}/update" "${SITE}/download" "${PENGYOS_SITE_REMOTE}"
fi

say "done: ${TARGETS[*]} at ${VERSION}"
