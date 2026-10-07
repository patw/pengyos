#!/bin/sh
# Write /usr/lib/os-release (and /etc/os-release, a link to it) for one
# PengyOS edition. Run from each edition's post-build.sh:
#     sh "$(dirname "$0")/../common/os-release.sh" "${TARGET_DIR}" Desktop desktop
#
# Versions are calendar-based, YY.MM.N (see VERSION at the repository root):
#   VERSION_ID     26.09     the release line: year and month
#   IMAGE_VERSION  26.09.0   the image: N counts images published that month
#   BUILD_ID       <commit>  the git commit it was built from (-dirty if the
#                            tree had uncommitted changes)
# IMAGE_ID/IMAGE_VERSION are the os-release fields image-based systems use
# (systemd-sysupdate, pengyos-release). /etc/issue is written here too.
set -eu
TARGET_DIR="$1" _variant="$2" _variant_id="$3"
_top="$(cd "$(dirname "$0")/../../.." && pwd)"
_image_version="$(tr -d ' \n' < "${_top}/VERSION")"
case "$_image_version" in
    [0-9][0-9].[0-9][0-9].[0-9]*) ;;
    *) echo "os-release.sh: VERSION must be YY.MM.N, got '${_image_version}'" >&2; exit 1 ;;
esac
_version_id="${_image_version%.*}"
_build_id="$(git -C "$_top" describe --always --dirty --abbrev=10 2>/dev/null || echo unknown)"

mkdir -p "${TARGET_DIR}/usr/lib"
cat > "${TARGET_DIR}/usr/lib/os-release" <<OSREL
NAME="PengyOS"
ID=pengyos
VERSION="${_image_version} (${_variant})"
VERSION_ID="${_version_id}"
PRETTY_NAME="PengyOS ${_variant} ${_version_id}"
VARIANT="${_variant}"
VARIANT_ID=${_variant_id}
IMAGE_ID=pengyos-${_variant_id}
IMAGE_VERSION="${_image_version}"
BUILD_ID="${_build_id}"
HOME_URL="https://pengyos.catbee.ca/"
DOCUMENTATION_URL="https://pengyos.catbee.ca/docs/"
ANSI_COLOR="1;36"
OSREL
# /etc/os-release is the standard link to it (a regular file there would be
# read first and could go stale).
rm -f "${TARGET_DIR}/etc/os-release"
ln -s ../usr/lib/os-release "${TARGET_DIR}/etc/os-release"
# /etc/issue, above the console login prompt: plain text, no agetty escapes,
# so `cat /etc/issue` on a new machine says what it is (as Ubuntu's and
# Debian's do). It was "\S{PRETTY_NAME} \n \l", which cat printed verbatim.
printf 'PengyOS %s %s\n\n' "${_variant}" "${_image_version}" > "${TARGET_DIR}/etc/issue"
echo "post-build: os-release: PengyOS ${_variant} ${_image_version} (${_build_id})"
