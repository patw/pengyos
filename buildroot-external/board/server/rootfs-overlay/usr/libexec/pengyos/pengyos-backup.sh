#!/bin/bash
# ============================================================================
# PengyOS Server — pengyos-backup.sh (timestamped tarball backups)
# ----------------------------------------------------------------------------
# The Server's backup routine. Archives the configured source trees into a
# timestamped tarball under a backup destination and prunes anything older than
# a rolling retention count. Run by pengyos-backup.service, which is driven by
# pengyos-backup.timer (the authoritative cadence).
#
# The single argument is the path to /etc/pengyos/backup.conf; a missing config
# falls back to sane defaults so the service still runs if the conf is absent.
#
# Uses only coreutils (date, mkdir, rm, ls, sort, tail, du) + tar/gzip, which
# ship on the systemd base — no extra packages required.
# ============================================================================
set -euo pipefail

# --- defaults (overridden by the conf) --------------------------------------
BACKUP_DEST="/var/backups/pengyos"
BACKUP_SOURCES="/etc /home"
RETENTION_COUNT=7
COMPRESS=yes

# --- load configuration ------------------------------------------------------
load_conf() {
    local conf="$1"
    [ -f "$conf" ] || return 0
    local line key value
    while IFS='=' read -r key value; do
        case "$key" in
            ''|\#*) continue ;;
        esac
        key="${key//[[:space:]]/}"
        value="${value%%#*}"
        value="${value#"${value%%[![:space:]]*}"}"   # ltrim
        value="${value%"${value##*[![:space:]]}"}"     # rtrim
        case "$key" in
            BACKUP_DEST)       BACKUP_DEST="$value" ;;
            BACKUP_SOURCES)    BACKUP_SOURCES="$value" ;;
            RETENTION_COUNT)   RETENTION_COUNT="$value" ;;
            COMPRESS)          COMPRESS="$value" ;;
            *) : ;;  # unknown keys are ignored
        esac
    done < "$conf"
}

# --- helpers --------------------------------------------------------------
now_stamp() { date +%Y%m%d-%H%M%S; }
extension() { [ "${COMPRESS,,}" = "yes" ] && echo "tar.gz" || echo "tar"; }

# keep only the newest RETENTION_COUNT backup-* files in the dest dir
prune() {
    local dest="$1" keep="$2" old
    old="$(ls -1t "${dest}"/backup-*."$(extension)" 2>/dev/null | tail -n +$((keep + 1)))"
    local f
    while IFS= read -r f; do
        [ -n "$f" ] && rm -f "$f" && logger -t pengyos-backup "pruned $f" 2>/dev/null || true
    done <<< "$old"
}

# --- main ------------------------------------------------------------------
main() {
    local conf="${1:-/etc/pengyos/backup.conf}"
    load_conf "$conf"

    # validate retention count is a positive integer
    case "$RETENTION_COUNT" in
        ''|*[!0-9]*) echo "pengyos-backup: RETENTION_COUNT must be a positive integer" >&2; RETENTION_COUNT=7 ;;
    esac
    [ "$RETENTION_COUNT" -ge 1 ] || RETENTION_COUNT=1

    mkdir -p "$BACKUP_DEST"

    # resolve existing source dirs (skip the missing ones with a warning)
    local sources=() src
    for src in $BACKUP_SOURCES; do
        if [ -d "$src" ]; then
            sources+=("$src")
        else
            echo "pengyos-backup: skipping non-existent source $src" >&2
        fi
    done

    if [ "${#sources[@]}" -eq 0 ]; then
        echo "pengyos-backup: no existing sources to back up; skipping archive" >&2
    else
        local ext stamp dest
        ext="$(extension)"
        stamp="$(now_stamp)"
        dest="${BACKUP_DEST}/backup-${stamp}.${ext}"

        # tar the sources; gzip if requested. --ignore-failed-read keeps a partial
        # archive alive if one source disappears mid-run rather than failing hard.
        if [ "${COMPRESS,,}" = "yes" ]; then
            tar -czf "$dest" --ignore-failed-read "${sources[@]}"
        else
            tar -cf "$dest" --ignore-failed-read "${sources[@]}"
        fi

        local size
        size="$(du -h "$dest" 2>/dev/null | cut -f1 || echo "?")"
        echo "pengyos-backup: wrote ${dest} (${size})"
        logger -t pengyos-backup "backup ${dest} (${size})" 2>/dev/null || true
    fi

    # retention always runs, even when there was nothing new to archive, so a
    # stale destination is still pruned on the scheduled cadence.
    prune "$BACKUP_DEST" "$RETENTION_COUNT"
}

main "$@"
