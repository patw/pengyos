#!/bin/sh
# PengyOS Core — post-image: stamp the rootfs UUID into grub.cfg + genimage.cfg,
# then assemble the bootable disk image with genimage.
set -e

BOARD_DIR=$(dirname "$0")

# Root filesystem UUID -> used as PARTUUID so the kernel finds root by UUID
# (stable regardless of which USB port / device node the stick gets).
UUID=$(dumpe2fs "$BINARIES_DIR/rootfs.ext2" 2>/dev/null | sed -n 's/^Filesystem UUID: *\(.*\)/\1/p')
[ -n "$UUID" ] || { echo "post-image: could not read rootfs UUID" >&2; exit 1; }

. "$BOARD_DIR/../common/image-ids.sh"
sed -e "s/DISK_UUID_TMP/$(image_uuid "$UUID" disk)/" \
    -e "s/BOOT_UUID_TMP/$(image_uuid "$UUID" boot)/" \
    -e "s/ESP_ID_TMP/$(image_uuid "$UUID" efi-part | cut -c1-8)/" \
    -e "s/UUID_TMP/$UUID/g" \
    "$BOARD_DIR/genimage.cfg" > "$BINARIES_DIR/genimage.cfg"
sed -i "s/UUID_TMP/$UUID/g" "$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"

support/scripts/genimage.sh -c "$BINARIES_DIR/genimage.cfg"
