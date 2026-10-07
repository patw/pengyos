#!/bin/sh
# installer-image.sh EDITION IMAGE SIGNATURE ROOT_PARTUUID
#
# Called by an edition's post-image.sh (cwd = the Buildroot tree, BINARIES_DIR
# and HOST_DIR set) after its own disk image is built: builds the install
# payload and the installer USB stick. Server and Desktop share it.
#   EDITION        recorded in the payload manifest (desktop, server)
#   IMAGE          the stick's file name in BINARIES_DIR
#   SIGNATURE      the stick's MBR disk signature (hybrid table)
#   ROOT_PARTUUID  the rootfs UUID the edition's grub.cfg boots
set -e
EDITION=$1 IMAGE=$2 SIGNATURE=$3 UUID=$4
[ -n "$UUID" ] || { echo "installer-image.sh: usage: EDITION IMAGE SIGNATURE ROOT_PARTUUID" >&2; exit 2; }
COMMON_DIR=$(dirname "$0")
. "$COMMON_DIR/image-ids.sh"

# The stick is the edition's own disk image (it boots the same live system) plus
# a PENGYOS-PAYLOAD partition that turns on the installer (the Desktop's GUI, the
# Server's console installer on tty1). The payload is what
# `pengyos-install image` lays onto a disk in the partitioned layout of
# docs/state-layout.md — the same artifacts an update will ship:
#   rootfs.ext4.zst  the root filesystem, byte for byte (goes into a root slot)
#   esp.vfat.zst     a 256 MiB ESP: GRUB + slot-a/bzImage (the installer adds
#                    grub.cfg + grubenv for the machine's own partitions)
#   manifest.json    sha256 of each file, compressed and raw
PAYLOAD="$BINARIES_DIR/payload"
rm -rf "$PAYLOAD" && mkdir -p "$PAYLOAD"

ESP="$BINARIES_DIR/esp.vfat"
rm -f "$ESP"
truncate -s 256M "$ESP"
# --invariant: mkfs.fat otherwise stamps the volume-label entry with the wall
# clock (it must come before -i, which it would reset to a constant).
"$HOST_DIR/sbin/mkfs.vfat" -F 32 -n PENGYOS-ESP --invariant \
    -i "$(image_uuid "$UUID" esp | cut -c1-8)" "$ESP" > /dev/null
MTOOLS_SKIP_CHECK=1 "$HOST_DIR/bin/mmd" -i "$ESP" ::/EFI ::/EFI/BOOT ::/slot-a
for f in bootx64.efi grub-pengyos.pf2; do
    MTOOLS_SKIP_CHECK=1 "$HOST_DIR/bin/mcopy" -i "$ESP" "$BINARIES_DIR/efi-part/EFI/BOOT/$f" ::/EFI/BOOT/
done
MTOOLS_SKIP_CHECK=1 "$HOST_DIR/bin/mcopy" -i "$ESP" "$BINARIES_DIR/bzImage" ::/slot-a/bzImage

entry() {  # $1 = key, $2 = raw file, $3 = compressed name
    zstd -q -T0 -9 -f -o "$PAYLOAD/$3" "$2"
    printf '    "%s": {"file": "%s", "sha256": "%s", "raw_size": %s, "raw_sha256": "%s"}' \
        "$1" "$3" "$(sha256sum "$PAYLOAD/$3" | cut -d' ' -f1)" \
        "$(stat -c %s "$2")" "$(sha256sum "$2" | cut -d' ' -f1)"
}
# The image version (YY.MM.N, the repository's VERSION -- as in os-release's
# IMAGE_VERSION) and the commit it was built from.
VERSION=$(tr -d ' \n' < "$COMMON_DIR/../../../VERSION")
BUILD=$(git -C "$COMMON_DIR" describe --always --dirty --abbrev=10 2>/dev/null || echo unknown)
{
    printf '{\n  "format": "pengyos-layout-1",\n  "edition": "%s",\n' "$EDITION"
    printf '  "version": "%s",\n  "build": "%s",\n  "files": {\n' "$VERSION" "$BUILD"
    entry rootfs "$BINARIES_DIR/rootfs.ext2" rootfs.ext4.zst
    printf ',\n'
    entry esp "$ESP" esp.vfat.zst
    printf '\n  }\n}\n'
} > "$PAYLOAD/manifest.json"
SIZE_MB=$(( $(du -sm "$PAYLOAD" | cut -f1) + 64 ))
rm -f "$BINARIES_DIR/payload.ext4"
PAYLOAD_UUID=$(image_uuid "$UUID" payload-fs)
"$HOST_DIR/sbin/mkfs.ext4" -q -L PENGYOS-PAYLOAD -U "$PAYLOAD_UUID" -E hash_seed="$PAYLOAD_UUID" \
    -d "$PAYLOAD" "$BINARIES_DIR/payload.ext4" "${SIZE_MB}M"

sed -e "s/DISK_UUID_TMP/$(image_uuid "$UUID" installer-disk)/" \
    -e "s/BOOT_UUID_TMP/$(image_uuid "$UUID" installer-boot)/" \
    -e "s/PAYLOAD_UUID_TMP/$(image_uuid "$UUID" installer-payload)/" \
    -e "s/UUID_TMP/$UUID/g" -e "s/IMAGE_TMP/$IMAGE/g" -e "s/SIGNATURE_TMP/$SIGNATURE/g" \
    "$COMMON_DIR/genimage-installer.cfg.in" > "$BINARIES_DIR/genimage-installer.cfg"
support/scripts/genimage.sh -c "$BINARIES_DIR/genimage-installer.cfg"
echo "post-image: installer stick -> $BINARIES_DIR/$IMAGE (payload $VERSION)"
