#!/bin/sh
# Fail the build when the root filesystem is nearly full or too big for a slot.
#   rootfs-headroom.sh ROOTFS_IMAGE
#
# The rootfs is a fixed-size ext4 (BR2_TARGET_ROOTFS_EXT2_SIZE in the config
# layers). When it fills up, the image still builds, and the failure shows up
# later, at boot: on 2026-11-08 the first 26.11.9 Server booted with about 16 MB
# free and failed its QEMU boot check. Checking here stops the build at the
# image step instead, saying which layer to raise.
#
# PENGYOS_ROOTFS_MIN_FREE_MB  the least free space allowed (default 150)
# The image must also fit an installed root slot: 8 GiB (docs/state-layout.md).
set -eu

IMG=$1
MIN_FREE_MB=${PENGYOS_ROOTFS_MIN_FREE_MB:-150}
SLOT_MB=8192

info=$(dumpe2fs -h "$IMG" 2>/dev/null)
field() { printf '%s\n' "$info" | sed -n "s/^$1: *//p"; }
bs=$(field 'Block size')
total=$(field 'Block count')
free=$(field 'Free blocks')
[ -n "$bs" ] && [ -n "$total" ] && [ -n "$free" ] || {
    echo "rootfs-headroom: could not read $IMG" >&2; exit 1; }

size_mb=$(( total * bs / 1048576 ))
free_mb=$(( free * bs / 1048576 ))
pct=$(( free * 100 / total ))
echo "rootfs-headroom: ${free_mb} MiB free of ${size_mb} MiB (${pct}%)"

if [ "$size_mb" -gt "$SLOT_MB" ]; then
    echo "rootfs-headroom: ${size_mb} MiB does not fit an installed ${SLOT_MB} MiB root slot" >&2
    exit 1
fi
if [ "$free_mb" -lt "$MIN_FREE_MB" ]; then
    echo "rootfs-headroom: only ${free_mb} MiB free, under ${MIN_FREE_MB} MiB." >&2
    echo "  Raise BR2_TARGET_ROOTFS_EXT2_SIZE in this edition's last config layer" >&2
    echo "  (buildroot-external/configs/layers/), then recompose the defconfigs." >&2
    exit 1
fi
