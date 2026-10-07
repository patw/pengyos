#!/usr/bin/env bash
# Flash a PengyOS disk image to a USB stick / block device, then repair the GPT.
#
#   sudo scripts/flash-usb.sh <image.img> <device>          # e.g. dist/pengyos-core-x86_64.img /dev/sdb
#   sudo scripts/flash-usb.sh --yes <image.img> <device>    # skip the confirmation prompt
#
# For an installer stick flash the *-installer.img (e.g.
# pengyos-desktop-x86_64-installer.img): it boots the live desktop and
# opens the installer. The plain .img boots straight to a desktop, no installer.
#
# WHY THE GPT REPAIR (the 2026-09-21 bug, now baked in):
#   `dd` copies the image's GPT verbatim. On a device LARGER than the image the
#   image's *secondary* GPT ends up mid-disk, while the device's true end may
#   still hold a stale GPT from whatever was there before. Primary and
#   end-of-disk backup then disagree, and strict firmware (e.g. the Intel N100
#   "behoweather") refuses to enumerate the stick as bootable at all — even
#   though the image boots fine under lenient OVMF/QEMU.
#   `sgdisk -e` ("use all space") relocates the secondary GPT to the device end;
#   `sgdisk -v` must then report "No problems found".
#
# WHY THE PRE-WRITE WIPE (the 2026-09-25 bug, now baked in):
#   `sgdisk -e` relocates the NEW backup GPT, but it does NOT erase the PREVIOUS
#   image's structures. Flash a SMALLER image onto a stick that held a LARGER one
#   and an orphan "EFI PART" header survives beyond the new image (seen at LBA
#   4022375, ~1.92 GiB, left by an earlier 1.9G image). The N100 firmware then
#   refused to enumerate the stick at all — even though `sgdisk -v` said "No
#   problems found" and the image was byte-for-byte sound. Wiping the device
#   before the dd removed the stale structure and fixed it. (Same family as the
#   09-21 bug: a leftover GPT confusing the firmware.)
#
# WHY THE HYBRID MBR (the 2026-09-27 bug, now baked in):
#   A GPT-only stick (protective MBR, type 0xEE only) is "the fragile option":
#   some firmware refuses to enumerate it at all. The Intel N100 "behoweather"
#   (AMI BIOS ADLNV104) did NOT list our GPT-only stick in its F7 boot menu,
#   even though the SAME stick booted fine under OVMF. After `sgdisk -h 1` — a
#   HYBRID MBR+GPT (the 0xEE protective entry PLUS a real 0xEF EFI-System MBR
#   entry) — the N100 listed and booted it. This is the layout mainstream distro
#   ISOs ship: both partition tables, so picky firmware always finds a path.
#   `sgdisk -v` still reports "No problems found" afterward.
#
# This script wipes, writes the dd, repairs the GPT, verifies, and refuses the
# obvious ways to erase the wrong disk.
set -euo pipefail

usage() { echo "usage: sudo $0 [--yes] <image.img> <device>" >&2; exit 2; }

YES=0
if [ "${1:-}" = "--yes" ]; then YES=1; shift; fi
[ $# -eq 2 ] || usage
IMG="$1"
DEV="$2"

[ "$(id -u)" = 0 ] || { echo "error: run me with sudo (writes a raw block device)" >&2; exit 1; }
[ -f "$IMG" ] || { echo "error: no such image: $IMG" >&2; exit 1; }
[ -b "$DEV" ] || { echo "error: not a block device: $DEV" >&2; exit 1; }

# --- refuse to erase something that isn't a whole removable-ish disk -------------
case "$DEV" in
    /dev/sd[a-z]|/dev/vd[a-z]|/dev/nvme[0-9]n[0-9]|/dev/mmcblk[0-9]) ;;
    *) echo "error: $DEV is not a whole disk (want e.g. /dev/sdb, not /dev/sdb1)" >&2; exit 1 ;;
esac
ROOT_SRC="$(findmnt -no SOURCE / 2>/dev/null || true)"
if [ "$DEV" = "$ROOT_SRC" ]; then echo "error: $DEV is your root filesystem" >&2; exit 1; fi
if findmnt -rn -S "$DEV" >/dev/null 2>&1; then
    echo "error: $DEV is mounted (unmount it first)" >&2; exit 1
fi

# --- how much to wipe before writing (MiB) --------------------------------------
# Must exceed the end of ANY image previously flashed to this stick, or an orphan
# GPT header past that point can survive and hide the stick from firmware (the
# 2026-09-25 bug). blkdiscard (whole-device TRIM) is tried first; this is the
# fallback prefix size for devices that do not support discard.
WIPE_MIB="${PENGYOS_WIPE_MIB:-2560}"

echo "image:  $IMG  ($(stat -c '%s' "$IMG") bytes)"
echo "sha256: $(sha256sum "$IMG" | cut -d' ' -f1)"
echo "device:"
lsblk -o NAME,SIZE,TYPE,FSTYPE,LABEL,MODEL "$DEV"

if [ "$YES" != 1 ]; then
    echo
    echo "  *** THIS WILL OVERWRITE EVERYTHING ON $DEV ***  (press Ctrl-C to abort)"
    read -r -p "  type YES to continue: " _a
    [ "${_a:-}" = "YES" ] || { echo "aborted"; exit 1; }
fi

# --- unmount any partitions on the target ---------------------------------------
mapfile -t parts < <(lsblk -pno NAME "$DEV" | tail -n +2)
for p in "${parts[@]:-}"; do
    [ -n "$p" ] || continue
    umount "$p" 2>/dev/null && echo "unmounted $p" || true
done

# --- wipe stale structures so no orphan GPT can survive past the new image ------
echo
if command -v blkdiscard >/dev/null 2>&1 && blkdiscard -f "$DEV" 2>/dev/null; then
    echo ">> discarded the whole device (blkdiscard) — no stale GPT can survive"
else
    echo ">> wiping the first ${WIPE_MIB} MiB (removes stale GPT from any prior image)…"
    dd if=/dev/zero of="$DEV" bs=1M count="$WIPE_MIB" conv=fsync status=progress
    echo "   (flashed an image larger than ${WIPE_MIB} MiB before? re-run with PENGYOS_WIPE_MIB=<bigger>)"
fi

# --- write ----------------------------------------------------------------------
echo
echo ">> writing image…"
dd if="$IMG" of="$DEV" bs=8M conv=fsync status=progress
sync

# --- the fix: relocate the secondary GPT to the end of the device ---------------
echo
echo ">> repairing GPT (relocate secondary GPT to the end of $DEV)…"
sgdisk -e "$DEV"

# --- also make the MBR hybrid (the 2026-09-27 bug, now baked in) ----------------
# GPT-only is the fragile option; the N100 (behoweather) would not enumerate the
# stick until the MBR carried a real 0xEF EFI-System entry too (hybrid MBR+GPT).
# Only do this when partition 1 is the EFI System partition (every PengyOS image).
# NB: `sgdisk -i` prints the GUID *name* ("EFI system partition"), not the
# "EF00" type code — checking for "EF00" silently never matched, so the hybrid
# MBR was skipped and the N100 would not enumerate the stick (the very bug this
# step exists to fix). Match the description instead.
if sgdisk -i 1 "$DEV" 2>/dev/null | grep -qi "EFI system partition"; then
    echo
    echo ">> making the MBR hybrid (GPT + a 0xEF EFI-System MBR entry)…"
    sgdisk -h 1 "$DEV"
else
    echo ">> (skipped hybrid MBR: partition 1 is not an EF00 EFI System partition)"
fi

echo
echo ">> verifying GPT:"
sgdisk -v "$DEV"          # expect: "No problems found."
echo
echo ">> layout:"
sgdisk -p "$DEV" | tail -n 5

# --- check for stray GPT structures beyond the image ----------------------------
# The firmware trap is an orphan 'EFI PART' *past* the new image. sgdisk -v only
# validates the primary + the relocated backup and passes even with a stray, so
# scan the wiped prefix (image_end..wipe_end) and flag any aligned signature.
echo
echo ">> checking for stray GPT structures beyond the image…"
img_bytes="$(stat -c '%s' "$IMG")"
img_sectors=$(( (img_bytes + 511) / 512 ))
wipe_sectors=$(( WIPE_MIB * 2048 ))
if [ "$wipe_sectors" -gt "$img_sectors" ]; then
    strays="$(dd if="$DEV" bs=512 skip="$img_sectors" \
                 count=$(( wipe_sectors - img_sectors )) status=none \
               | grep -abo 'EFI PART' \
               | awk -F: -v base="$img_sectors" '$1 % 512 == 0 { print base + int($1 / 512) }' \
               | head -n 10)" || true
    if [ -n "${strays:-}" ]; then
        echo "   WARNING: stray GPT signature(s) beyond the image at LBA: ${strays//$'\n'/ }"
        echo "   -> wipe more (PENGYOS_WIPE_MIB=<bigger>) or zero the whole device, then re-run."
    else
        echo "   clean — no stray GPT beyond the image."
    fi
else
    echo "   (skipped: wipe range does not extend past the image)"
fi

echo
echo "done — $DEV is ready to boot."
