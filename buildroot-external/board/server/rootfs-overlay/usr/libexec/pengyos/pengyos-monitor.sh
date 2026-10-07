#!/bin/bash
# ============================================================================
# PengyOS Server — pengyos-monitor.sh (metrics collection)
# ----------------------------------------------------------------------------
# The Server's monitoring "stack". A minimal Server has no Prometheus/Grafana;
# this script samples the kernel's own views of the machine and appends one
# line per sample to a per-day log so `journalctl`/`tail -f` can watch it.
#
# It is run by pengyos-monitor.service, which is driven by pengyos-monitor.timer
# (the authoritative cadence). The single argument is the path to
# /etc/pengyos/monitor.conf; a missing config falls back to sane defaults so the
# service still runs if the conf is absent.
#
# Uses only coreutils (date, df, cat, mkdir, mv, rm, wc, sort, tr, grep) which
# ship on the systemd base — no extra packages required.
# ============================================================================
set -euo pipefail

# --- defaults (overridden by the conf) --------------------------------------
LOG_FILE="/var/log/pengyos-monitor.log"
SAMPLE_INTERVAL_SECS=300
DISK_TARGETS="/ /var /home"

# --- load configuration ------------------------------------------------------
# shellcheck disable=SC1091
load_conf() {
    local conf="$1"
    [ -f "$conf" ] || return 0
    local line key value
    while IFS='=' read -r key value; do
        # skip comments / blank lines
        case "$key" in
            ''|\#*) continue ;;
        esac
        # trim surrounding whitespace
        key="${key//[[:space:]]/}"
        value="${value%%#*}"
        value="${value#"${value%%[![:space:]]*}"}"   # ltrim
        value="${value%"${value##*[![:space:]]}"}"     # rtrim
        case "$key" in
            LOG_FILE) LOG_FILE="$value" ;;
            SAMPLE_INTERVAL_SECS) SAMPLE_INTERVAL_SECS="$value" ;;
            DISK_TARGETS) DISK_TARGETS="$value" ;;
            *) : ;;  # unknown keys are ignored
        esac
    done < "$conf"
}

# --- helpers --------------------------------------------------------------
now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# load average "1m 5m 15m" -> "1m=0.05 5m=0.10 15m=0.08"
load_avg() {
    local raw label
    raw="$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || true)"
    # /proc/loadavg fields are the 1m/5m/15m averages; label them as such.
    local labels=(1 5 15)
    local i=0 out=""
    for field in $raw; do
        out+=" ${labels[$i]}m=${field}"
        i=$((i + 1))
    done
    printf '%s' "${out# }"   # drop the leading space
}

mem_used_total() {
    # /proc/meminfo: MemTotal / MemAvailable (kB) -> "used=..kB total=..kB"
    local total available
    total="$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    available="$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
    local used=$((total - available))
    printf 'mem_used=%dkB mem_total=%dkB' "$used" "$total"
}

disk_usage() {
    # one "%used mount" per requested target that currently exists.
    # POSIX `df -P` prints exactly one data line per filesystem (no wrapping),
    # so Use% is field 5 and the mount point is field 6.
    local out="" target rest pct mount mlabel
    for target in $DISK_TARGETS; do
        [ -d "$target" ] || continue
        rest="$(df -P "$target" 2>/dev/null | tail -1)"
        [ -z "$rest" ] && continue
        pct="$(printf '%s\n' "$rest" | awk '{print $5}' | tr -d '%')"
        mount="$(printf '%s\n' "$rest" | awk '{print $6}')"
        [ -n "$pct" ] && [ -n "$mount" ] && {
            # normalise the mount point into a label ("/" -> "root")
            mlabel="${mount//\//_}"
            [ -z "$mlabel" ] && mlabel="root"
            out+="disk_${mlabel}=${pct}%"
        }
    done
    printf '%s' "$out"
}

conn_count() {
    # number of ESTABLISHED (TCP state 05) connections, summed over v4 + v6.
    # awk over both files gives a single total (grep -c would print per-file).
    local n
    n="$(awk '$4 == "05" {c++} END {print c+0}' /proc/net/tcp /proc/net/tcp6 2>/dev/null || echo 0)"
    printf 'established_conns=%s' "$n"
}

# --- main ------------------------------------------------------------------
main() {
    local conf="${1:-/etc/pengyos/monitor.conf}"
    load_conf "$conf"

    mkdir -p "$(dirname "$LOG_FILE")"

    local ts load mem disk conns line
    ts="$(now_iso)"
    load="$(load_avg)"
    mem="$(mem_used_total)"
    disk="$(disk_usage)"
    conns="$(conn_count)"
    line="${ts} ${load} ${mem} ${disk} ${conns}"

    # append the sample; if the daily log is missing, start it first.
    touch "$LOG_FILE"
    printf '%s\n' "$line" >> "$LOG_FILE"

    # also stream to the journal so `journalctl -u pengyos-monitor` shows it.
    logger -t pengyos-monitor "$line" 2>/dev/null || true

    # keep only the last 10k lines so a long-lived host can't fill the disk.
    local lines
    lines="$(wc -l < "$LOG_FILE" 2>/dev/null | tr -d ' ' || echo 0)"
    if [ "${lines:-0}" -gt 10000 ]; then
        tail -n 5000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
    fi
}

main "$@"
