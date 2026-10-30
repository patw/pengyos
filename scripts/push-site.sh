#!/usr/bin/env bash
# Push the published site dir to the live host, retrying while the host is away.
#
#   scripts/push-site.sh SITE REMOTE         REMOTE is host:path/ ($PENGYOS_SITE_REMOTE)
#
# release.sh --push calls this as its last step, after every image is signed and
# published into SITE. The remote is another machine with its own life: on
# 2026-10-30 miniserv rebooted at 02:57, in the middle of the act lane's push, ssh
# answered "System is going down", and 26.10.50 sat signed in the local site while
# the live feed stayed on 26.10.49. Nothing was wrong with the release; the push
# needed to wait a few minutes. `rsync -a` is idempotent, so a retry only copies
# what the failed attempt did not.
#
#   PENGYOS_PUSH_TRIES  attempts before giving up (default 5)
#   PENGYOS_PUSH_WAIT   seconds before the first retry, doubled each time
#                       (default 30: 30+60+120+240s covers a reboot)
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: push-site.sh SITE REMOTE" >&2; exit 2; }
SITE="$1" REMOTE="$2"
TRIES="${PENGYOS_PUSH_TRIES:-5}" WAIT="${PENGYOS_PUSH_WAIT:-30}"

n=1
until rsync -a "${SITE}/update" "${SITE}/download" "${REMOTE}"; do
    if [ "${n}" -ge "${TRIES}" ]; then
        echo "[release] push failed after ${n} attempts: the release is signed and published" \
             "in ${SITE} but not on ${REMOTE}; rerun scripts/push-site.sh to finish it" >&2
        exit 1
    fi
    echo "[release] push attempt ${n}/${TRIES} failed; retrying in ${WAIT}s" >&2
    sleep "${WAIT}"
    n=$((n + 1)) WAIT=$((WAIT * 2))
done
