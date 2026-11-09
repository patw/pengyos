# The installer as an ISO

Status: plan, 2026-11-09. Branch `feat/iso`.

## Why

The download today is a compressed raw disk image (`.img.xz`). Two reports
(2026-11-08):

- A Linux user `dd`'d it without unpacking, then without the `wipefs` and
  `sgdisk -e` steps, and the stick did not boot. "Why isn't it like Ubuntu?"
- A VM-farm admin could not run it on any of his hypervisors. Every hypervisor
  installs from an ISO in its virtual CD drive. Our live system finds its root on
  a *partition* of the stick, and a virtual CD drive exposes no partitions.
  (Separately, the Hyper-V and VMware disk drivers were missing from the kernel.
  Fixed on main, bcb51766.)

An `.iso` that is also a valid disk image (a hybrid ISO, as Debian, Ubuntu and
Fedora ship) works for both: `dd`, Etcher, Rufus (ISO or DD mode), Ventoy,
GNOME Disks, and every hypervisor's CD drive.

## Shape

```
pengyos-<edition>-<version>-x86_64.iso   (ISO 9660 + Rock Ridge, label PENGYOS_<EDITION>)
  EFI/BOOT/BOOTX64.EFI, grub.cfg         GRUB, finds the medium by ISO label
  boot/bzImage, boot/initrd.img          kernel + a small initramfs (busybox)
  live/rootfs.erofs                      the live system, compressed (lz4hc)
  payload/manifest.json                  the image to install: the same three
  payload/rootfs.ext4.zst                files installer-image.sh makes today
  payload/esp.vfat.zst
  + El Torito UEFI boot image (efiboot.img, FAT) and an appended ESP partition
    in the hybrid GPT/MBR, so firmware boots it from CD and from USB
```

Built with host-xorriso (`-as mkisofs ... -append_partition 2 0xef efiboot.img`),
host-erofs-utils (both in Buildroot 2026.08.1).

## Boot

1. Firmware → GRUB (from the El Torito image on CD, the appended ESP on USB).
2. GRUB loads `bzImage` + `initrd.img` with `pengyos.live=PENGYOS_<EDITION>`.
3. The initramfs `/init` waits for a block device (sr*, sd*, vd*, nvme*) with that
   ISO label, mounts it read-only at `/run/pengyos/medium`, loop-mounts
   `live/rootfs.erofs`, puts an overlay on top with a tmpfs upper (the live
   session's changes live in RAM, as on every live ISO), moves the medium mount
   into the new root and `switch_root`s.
4. The live system bind-mounts `/run/pengyos/medium/payload` to
   `/run/pengyos/payload` (pengyos-install's `PAYLOAD_MOUNT`): the installer
   itself does not change.

The live test stays the kernel line (no `pengyos.data=`, see
scripts/tests/test_live_boot_detection.py). "An installer is present" moves from
`/dev/disk/by-label/PENGYOS-PAYLOAD` to `/run/pengyos/payload/manifest.json`:
pengyos-live-installer.service, live-installer, console-installer,
pengyos-disks' system labels, and pengyos-install's own medium checks (it must
still refuse to install onto the medium it booted from, now found through
`/run/pengyos/medium`).

## Kernel

`CONFIG_EROFS_FS=y` with LZ4 (the live root is mounted before any module can
load). sr, iso9660, loop, overlay, devtmpfs and initrd are already built in.
Installed systems keep booting ext4 slots with no initramfs: nothing changes for
them, and updates are untouched.

## Size

The live system shrinks about 2.3× (measured on 26.11.9's NVIDIA Desktop tree:
4108 MiB → 1809 MiB with lz4hc), so the NVIDIA Desktop medium goes from 5.9 GB
written to about 3 GB, and the ISO is published uncompressed (no `.xz`).

## Steps

1. Prototype outside the build: assemble an ISO from a finished build's
   artifacts with a script, boot it in QEMU as a CD and as a USB disk.
2. Kernel option, initramfs, post-image ISO assembly; the live-installer and
   console-installer changes; pengyos-install's medium detection.
3. Release: publish `.iso` beside `.img.xz` for one release, then only `.iso`;
   the release boot check boots the ISO; the download page and docs/vm.html.
4. Real hardware: behoweather (the strict N100 firmware that needed the hybrid
   MBR and the GPT fixes), beholaptop, a Proxmox VM, and a VMware/Hyper-V host
   if one can be borrowed.

Open questions: keep `.img.xz` for Raspberry Pi Imager users (it takes ISOs too);
whether the Server's console installer should offer "copy to RAM" so the stick
can be pulled; Secure Boot stays out of scope.
