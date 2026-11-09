#!/usr/bin/env bash
# PROTOTYPE: assemble a hybrid live/installer ISO from a finished edition build.
#   make-iso.sh EDITION KERNEL OUT.iso      (EDITION: server, desktop, ...)
# Needs mkfs.erofs, xorriso, a static busybox, mkfs.vfat + mcopy.
set -euo pipefail
export PATH=$PATH:/usr/sbin:/sbin
ed=$1; kernel=$2; out=$3
here=$(cd "$(dirname "$0")" && pwd)
O=/data/build/pengyos/output/pengyos-$ed
I=$O/images
work=$(mktemp -d /data/tmp/isoproto/work-$ed.XXXX)
stage=$work/stage
trap 'rm -rf "$work"' EXIT

label=$(echo "PENGYOS_${ed^^}" | tr '-' '_')
id=$(dumpe2fs -h "$I/rootfs.ext2" 2>/dev/null | sed -n 's/^Filesystem UUID: *//p')
[ -n "$id" ] || { echo "no rootfs UUID" >&2; exit 1; }
mkdir -p "$stage"/{EFI/BOOT,boot,live,pengyos,payload}
echo "$id" > "$stage/pengyos/medium-id"

echo "[iso] live root: erofs (lz4hc) from rootfs.tar"
mkfs.erofs -d0 -zlz4hc,12 --tar=f "$stage/live/rootfs.erofs" "$I/rootfs.tar"

echo "[iso] payload"
cp "$I"/payload/* "$stage/payload/"

echo "[iso] initramfs"
ird=$work/initramfs; mkdir -p "$ird"/{bin,dev,proc,sys,run,medium,lower,rw,newroot}
cp /usr/bin/busybox "$ird/bin/busybox"
install -m0755 "$here/init" "$ird/init"
(cd "$ird" && find . | cpio -o -H newc --quiet | gzip -9) > "$stage/boot/initrd.img"
cp "$kernel" "$stage/boot/bzImage"

echo "[iso] GRUB"
cp "$I/efi-part/EFI/BOOT/grub-pengyos.pf2" "$stage/EFI/BOOT/"
extra=""; [ "$ed" = desktop ] || [ "$ed" = desktop-nvidia ] && extra="splash plymouth.ignore-serial-consoles"
cat > "$work/early.cfg" <<CFG
search --no-floppy --set=root --file /pengyos/medium-id
set prefix=(\$root)/EFI/BOOT
configfile \$prefix/grub.cfg
CFG
# the live menu: the stick's own menu, with the ISO's boot line
sed -n '1,/^menuentry/p' "$I/efi-part/EFI/BOOT/grub.cfg" | sed '$d' > "$stage/EFI/BOOT/grub.cfg"
cat >> "$stage/EFI/BOOT/grub.cfg" <<CFG
menuentry "PengyOS ${ed} (live)" {
    linux /boot/bzImage pengyos.live=$id rw audit=0 $extra console=tty0 \${pengyos_serial}
    initrd /boot/initrd.img
}
CFG
GD=$(ls -d $O/build/grub2-*/build-x86_64-efi/grub-core)
$O/host/bin/grub-mkimage -d "$GD" -O x86_64-efi -p /EFI/BOOT -c "$work/early.cfg" -o "$stage/EFI/BOOT/BOOTX64.EFI" \
    boot linux ext2 fat iso9660 part_msdos part_gpt normal efi_gop gfxterm gfxmenu font all_video videoinfo \
    loadenv test smbios serial search search_fs_file search_label configfile echo
mkfs.vfat -C -n PENGYOS_EFI "$work/efiboot.img" 4096 >/dev/null
mmd -i "$work/efiboot.img" ::/EFI ::/EFI/BOOT
mcopy -i "$work/efiboot.img" "$stage/EFI/BOOT/BOOTX64.EFI" ::/EFI/BOOT/BOOTX64.EFI

echo "[iso] xorriso -> $out"
xorriso -as mkisofs -quiet -iso-level 3 -r -J -joliet-long -V "$label" -o "$out" \
    -partition_offset 16 -append_partition 2 0xef "$work/efiboot.img" \
    -e --interval:appended_partition_2:all:: -no-emul-boot \
    "$stage"
ls -l "$out"
