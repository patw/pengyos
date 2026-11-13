#!/usr/bin/env bash
# Rebuild, check and (optionally) publish PengyOS images in one go.
#
#   scripts/release.sh [options] [TARGET ...]        default: all five images
#
#   --publish        sign and publish into the local site dir: update-publish for
#                    the A/B editions (Desktop, Desktop NVIDIA, Server, Server
#                    NVIDIA) and one
#                    download-publish for every target built but Core (staged: the
#                    download page lists only releases promoted to stable).
#                    Needs $PENGYOS_RELEASE_KEY.
#   --push           rsync the site dir to $PENGYOS_SITE_REMOTE (host:path/),
#                    retrying while the host is away (scripts/push-site.sh).
#                    Implies --publish.
#   --channel C      update channel for --publish (default: testing)
#   --notes FILE     release notes for update-publish (required with --publish:
#                    the update page, the feed and the Updates app show them).
#                    Short plain text, no Markdown: a one-line summary, a blank
#                    line, then a few "- " lines; under 600 characters. Checked
#                    before anything builds (pengyos-release notes-check).
#   --skip-gate      publish without the whole-repo gate (see below); for an
#                    urgent security fix while an unrelated suite is red
#   --skip-cosmic    don't rebuild COSMIC before a Desktop target
#   --no-boot-check  skip the QEMU checks (automatic, with a warning, on a host
#                    without KVM: software emulation is too slow for them)
#   --allow-dirty    build from a tree with uncommitted changes (BUILD_ID gets
#                    "-dirty"; refused with --publish)
#
# Steps per target: acquire (blobless clone + pinned tag of Buildroot; the only
# network step) -> configure -> build -> SBOM (generated and embedded, so the
# image carries a current one) -> boot check under QEMU (the smoke matrix for
# Core and Server; for the Desktops a login check plus `pengyos-qemu-verify
# desktop`, which proves the COSMIC session runs). Nothing is signed or pushed
# unless asked: signing a release and putting it in front of machines is a
# human decision (CLAUDE.md, "Security boundary").
#
# With --publish, the whole-repo gate (pengyos_gate.py --base none, every suite,
# about 10 minutes) runs first and a red gate stops the release before anything
# builds. It replaced GitHub CI on 2026-11-11: the landing gate only runs the
# suites a change touches, and CI, the only whole-repo run, stayed red for two
# weeks unnoticed. The nightly runs the same gate and reports it.
#
# Bump VERSION and commit before running: images take IMAGE_VERSION from it and
# BUILD_ID from the commit.
set -euo pipefail

# Published artifacts must stay world-readable: the site is served by another
# user (nginx on miniserv), so a restrictive umask here silently 403s the whole
# feed. It happened: an agent-run release under umask 0077 published every
# 26.10.33 file as -rw------- and -rwx------, the --push copied those modes, and
# `pengyos-update check` got 403 from the live feed until they were chmodded.
umask 022

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGETS_FILE="${REPO}/targets.toml"
BUILD_ROOT="${PENGYOS_BUILD_ROOT:-$HOME/build/pengyos}"
SITE="${PENGYOS_SITE:-${BUILD_ROOT}/site}"
CHANNEL=testing NOTES="" PUBLISH=0 PUSH=0 SKIP_COSMIC=0 ALLOW_DIRTY=0 BOOT_CHECK=1 SKIP_GATE=0
TARGETS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --publish) PUBLISH=1 ;;
        --push) PUBLISH=1; PUSH=1 ;;
        --channel) CHANNEL="$2"; shift ;;
        --notes) NOTES="$2"; shift ;;
        --skip-gate) SKIP_GATE=1 ;;
        --skip-cosmic) SKIP_COSMIC=1 ;;
        --no-boot-check) BOOT_CHECK=0 ;;
        --allow-dirty) ALLOW_DIRTY=1 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
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
    # The notes are the release's description on the update page, in the feed and in
    # the Updates app; without them a release says nothing about what changed.
    [ -s "${NOTES}" ] || die "--publish needs --notes FILE (non-empty): the update page, the feed and the Updates app show it"
    # Checked now, not at update-publish: a failure there comes after hours of building.
    (cd "${REPO}/tools/pengyos-release" && python3 -m pengyos_release notes-check "$(realpath "${NOTES}")") \
        || die "fix the release notes first"
    if [ "${SKIP_GATE}" = 1 ]; then
        echo "[release] WARNING: --skip-gate: publishing without the whole-repo gate" >&2
    else
        say "whole-repo gate (every suite; about 10 minutes)"
        # The box's ~/.local/bin/cargo is a stub; rustup's must come first.
        (cd "${REPO}" && PATH="$HOME/.cargo/bin:$PATH" python3 scripts/pengyos_gate.py run \
            --repo . --mode full --base none) \
            || die "the whole-repo gate is red: fix it (or --skip-gate for an urgent fix)"
    fi
fi
[ "${PUSH}" = 0 ] || [ -n "${PENGYOS_SITE_REMOTE:-}" ] || die "--push needs PENGYOS_SITE_REMOTE (host:path/)"
if [ "${BOOT_CHECK}" = 1 ] && [ ! -w /dev/kvm ]; then
    echo "[release] WARNING: no writable /dev/kvm — skipping the QEMU boot checks" >&2
    BOOT_CHECK=0
fi
[ "${PUBLISH}" = 1 ] && [ "${BOOT_CHECK}" = 0 ] && die "refusing to publish images that were not boot-checked"
# A test build from a branch worktree in these output trees records that worktree's
# patch paths, and legal-info (the SBOM step) copies patches from them again: a
# removed worktree killed 26.11.15 at Desktop's SBOM, hours in. Checked before building.
STALE_OUTPUTS=()
for t in "${TARGETS[@]}"; do STALE_OUTPUTS+=("${BUILD_ROOT}/output/${t}"); done
python3 "${REPO}/scripts/check-applied-patches.py" --repo "${REPO}" "${STALE_OUTPUTS[@]}" \
    || die "output trees hold patches from outside this repo (a worktree test build?): dirclean those packages"

build() { (cd "${REPO}/tools/pengyos-build" && python3 -m pengyos_build "$@" --targets "${TARGETS_FILE}"); }
verify() { (cd "${REPO}/tools/pengyos-qemu-verify" && python3 -m pengyos_qemu_verify "$@"); }
out() { echo "${BUILD_ROOT}/output/$1"; }

say "PengyOS ${VERSION} ($(git -C "${REPO}" describe --always --dirty)) — ${TARGETS[*]}"

case " ${TARGETS[*]} " in
    *" pengyos-desktop"*)
        if [ "${SKIP_COSMIC}" = 0 ]; then
            say "PengyOS libcosmic apps (scripts/build-cosmic.sh)"
            "${REPO}/scripts/build-cosmic.sh"
        fi ;;
    *" pengyos-server"*)
        # No Desktop in this release, but the Server still ships the terminal
        # Hardware Monitor, which is host-built the same way (no libcosmic).
        if [ "${SKIP_COSMIC}" = 0 ]; then
            say "Server Hardware Monitor (scripts/build-cosmic.sh pengyos-hwmon-tui)"
            "${REPO}/scripts/build-cosmic.sh" pengyos-hwmon-tui
        fi ;;
esac

for t in "${TARGETS[@]}"; do
    say "${t}: acquire + configure + build"
    build acquire "${t}"
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
           verify smoke --matrix smoke/server.toml --image "$(out "${t}")/images/pengyos-server-x86_64.img"
           # The installer ISO, the main download, from a CD drive as a VM boots it:
           # the initramfs has to find the medium and start the live system.
           verify boot "$(out "${t}")/images/pengyos-server-x86_64.iso" --uefi --ram 2048 --timeout 240 ;;
        *) verify boot "$(out "${t}")/images/pengyos-desktop-x86_64.img" --uefi --ram 2048 --timeout 240
           # A login prompt is not a desktop: check the COSMIC session on BOTH desktop
           # images. Only the installer stick was ever session-checked, so 26.10.15's
           # plain live image - the one a person actually boots - shipped stopping at
           # the greeter and nothing noticed. The live image has no installer payload,
           # so it is checked without that one process.
           verify desktop "$(out "${t}")/images/pengyos-desktop-x86_64.img" \
               --no-installer --out "$(out "${t}")/images/qemu-live-check"
           verify desktop "$(out "${t}")/images/pengyos-desktop-x86_64-installer.img" \
               --out "$(out "${t}")/images/qemu-desktop-check"
           # The installer ISO from a CD drive: live session up and the installer open.
           verify desktop "$(out "${t}")/images/pengyos-desktop-x86_64.iso" \
               --out "$(out "${t}")/images/qemu-iso-check" ;;
    esac
done

if [ "${PUBLISH}" = 1 ]; then
    rel() { (cd "${REPO}/tools/pengyos-release" && python3 -m pengyos_release "$@"); }
    notes=(); [ -n "${NOTES}" ] && notes=(--notes "@${NOTES}")
    dl=()
    for t in "${TARGETS[@]}"; do
        # Core is built and smoke-tested (it is the base) but not offered for
        # download or update: no installer, no SSH key, no updater yet.
        [ "${t}" = pengyos-core ] && continue
        dl+=(--build "$(out "${t}")")
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
    "${REPO}/scripts/push-site.sh" "${SITE}" "${PENGYOS_SITE_REMOTE}"   # retries a host that is away
fi

say "done: ${TARGETS[*]} at ${VERSION}"
