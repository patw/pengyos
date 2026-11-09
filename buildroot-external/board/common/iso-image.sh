#!/bin/sh
# iso-image.sh EDITION ISO ROOT_PARTUUID
#
# Called by an edition's post-image.sh after installer-image.sh (which made
# BINARIES_DIR/payload): builds the installer as a hybrid ISO, the download
# people write with plain dd or attach to a VM's CD drive (docs/iso-installer.md).
#   EDITION        desktop | server (the label and the live boot line)
#   ISO            its file name in BINARIES_DIR
#   ROOT_PARTUUID  the rootfs UUID: also the medium id the initramfs looks for
#
#   EFI/BOOT/BOOTX64.EFI, grub.cfg   GRUB with iso9660, finds the medium by file
#   boot/bzImage, boot/initrd.img    the kernel + busybox initramfs (iso/init)
#   live/rootfs.erofs                the live system (BR2_TARGET_ROOTFS_EROFS)
#   payload/                         what the installer lays onto a disk
#   pengyos/medium-id                ROOT_PARTUUID
# plus a FAT El Torito image, also appended as an MBR 0xEF partition so UEFI
# firmware boots it from a CD and from a USB stick (behoweather's strict N100
# firmware lists it; 2026-11-09).
set -e
EDITION=$1 ISO=$2 ID=$3
[ -n "$ID" ] || { echo "iso-image.sh: usage: EDITION ISO ROOT_PARTUUID" >&2; exit 2; }
COMMON_DIR=$(cd "$(dirname "$0")" && pwd)
for f in rootfs.erofs initramfs-busybox bzImage payload/manifest.json efi-part/EFI/BOOT/grub.cfg; do
    [ -e "$BINARIES_DIR/$f" ] || { echo "iso-image.sh: missing $BINARIES_DIR/$f" >&2; exit 1; }
done
GRUB_CORE=$(ls -d "$BUILD_DIR"/grub2-*/build-x86_64-efi/grub-core | head -1)
[ -f "$GRUB_CORE/iso9660.mod" ] || { echo "iso-image.sh: no GRUB x86_64-efi modules in $BUILD_DIR" >&2; exit 1; }
export MTOOLS_SKIP_CHECK=1

WORK="$BINARIES_DIR/iso-work"
STAGE="$WORK/stage"
rm -rf "$WORK" && mkdir -p "$STAGE/EFI/BOOT" "$STAGE/boot" "$STAGE/live" "$STAGE/pengyos"
echo "$ID" > "$STAGE/pengyos/medium-id"
# Hard links: the ISO tree is read once by xorriso, no need for copies.
ln "$BINARIES_DIR/rootfs.erofs" "$STAGE/live/rootfs.erofs"
cp -al "$BINARIES_DIR/payload" "$STAGE/payload"
ln "$BINARIES_DIR/bzImage" "$STAGE/boot/bzImage"

# initramfs: init + static busybox, fixed mtimes so it is the same every build
IRD="$WORK/initramfs"
mkdir -p "$IRD/bin" "$IRD/dev" "$IRD/proc" "$IRD/sys" "$IRD/run" "$IRD/medium" "$IRD/lower" "$IRD/rw" "$IRD/newroot"
install -m 0755 "$BINARIES_DIR/initramfs-busybox" "$IRD/bin/busybox"
install -m 0755 "$COMMON_DIR/iso/init" "$IRD/init"
find "$IRD" -exec touch -h -d @0 {} +
(cd "$IRD" && find . | LC_ALL=C sort | cpio -o -H newc --quiet -R 0:0 | gzip -9n) > "$STAGE/boot/initrd.img"

# GRUB: the stick's own menu preamble (hidden menu, font, serial detection) and
# the edition's own kernel arguments, booted from the ISO.
cp "$BINARIES_DIR/efi-part/EFI/BOOT/grub-pengyos.pf2" "$STAGE/EFI/BOOT/"
SRC_CFG="$BINARIES_DIR/efi-part/EFI/BOOT/grub.cfg"
ARGS=$(sed -n 's/^[[:space:]]*linux \/bzImage //p' "$SRC_CFG" | head -1 |
       sed -e 's/root=PARTUUID=[^ ]*//' -e 's/\brootwait\b//' -e 's/  */ /g' -e 's/^ //')
TITLE=$(sed -n 's/^PRETTY_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$TARGET_DIR/usr/lib/os-release")
{
    sed -n '1,/^menuentry/p' "$SRC_CFG" | sed '$d'
    printf 'menuentry "%s (live and installer)" {\n' "${TITLE:-PengyOS}"
    printf '    linux /boot/bzImage pengyos.live=%s %s\n' "$ID" "$ARGS"
    printf '    initrd /boot/initrd.img\n}\n'
} > "$STAGE/EFI/BOOT/grub.cfg"
cat > "$WORK/early.cfg" <<CFG
search --no-floppy --set=root --file /pengyos/medium-id
set prefix=(\$root)/EFI/BOOT
configfile \$prefix/grub.cfg
CFG
"$HOST_DIR/bin/grub-mkimage" -d "$GRUB_CORE" -O x86_64-efi -p /EFI/BOOT -c "$WORK/early.cfg" \
    -o "$STAGE/EFI/BOOT/BOOTX64.EFI" \
    boot linux ext2 fat iso9660 part_msdos part_gpt normal efi_gop gfxterm gfxmenu font all_video \
    videoinfo loadenv test smbios serial search search_fs_file search_label configfile echo
EFIIMG="$WORK/efiboot.img"
truncate -s 4M "$EFIIMG"
"$HOST_DIR/sbin/mkfs.vfat" -n PENGYOS_EFI --invariant -i "$(echo "$ID" | tr -d - | cut -c1-8)" "$EFIIMG" > /dev/null
"$HOST_DIR/bin/mmd" -i "$EFIIMG" ::/EFI ::/EFI/BOOT
"$HOST_DIR/bin/mcopy" -i "$EFIIMG" "$STAGE/EFI/BOOT/BOOTX64.EFI" ::/EFI/BOOT/BOOTX64.EFI

LABEL=$(echo "PENGYOS_$EDITION" | tr 'a-z-' 'A-Z_')
DATE=""
[ -n "$SOURCE_DATE_EPOCH" ] && DATE="--modification-date=$(date -u -d "@$SOURCE_DATE_EPOCH" +%Y%m%d%H%M%S00)"
rm -f "$BINARIES_DIR/$ISO"
"$HOST_DIR/bin/xorriso" -as mkisofs -quiet -iso-level 3 -r -J -joliet-long -V "$LABEL" $DATE \
    -o "$BINARIES_DIR/$ISO" \
    -partition_offset 16 -append_partition 2 0xef "$EFIIMG" \
    -e --interval:appended_partition_2:all:: -no-emul-boot \
    "$STAGE"
rm -rf "$WORK"
echo "post-image: installer ISO -> $BINARIES_DIR/$ISO"
