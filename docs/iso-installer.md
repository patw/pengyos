# The installer as an ISO

Status: prototype works in QEMU, 2026-11-09. Branch `feat/iso`.

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

## Prototype results (2026-11-09)

`scripts/iso/make-iso.sh EDITION KERNEL OUT.iso` builds an ISO from a finished
edition build (`images/rootfs.tar`, `images/payload/`, the edition's GRUB build)
and a kernel with `CONFIG_EROFS_FS=y`; `scripts/iso/init` is the initramfs. Tools
for now from the box's Ubuntu (erofs-utils 1.9, xorriso 1.5.6, busybox-static,
dosfstools, mtools); the real build takes Buildroot's host packages.

Built from 26.11.10, in QEMU/KVM with OVMF:

| | Server | Desktop |
|---|---|---|
| ISO size | 851 MiB (installer stick: 1.9 GB) | 2.9 GiB (installer stick: 5.0 GiB) |
| CD (ide-cd) | boots, console installer starts | boots, live desktop, installer opens |
| USB stick | boots | not tried yet |
| plain disk (virtio-blk) | boots | not tried yet |
| install from the ISO | `pengyos-install image` onto a blank 40 GB disk, then the disk boots alone | not tried yet |

- The installer already refuses the medium: it is mounted, so `pengyos-install
  disks` lists it as "running system — not installable".
- podman cannot keep its storage on overlayfs: the initramfs mounts a tmpfs on
  `/var/lib/containers` for the live session.
- The Desktop runs on virtio-gpu without 3D (llvmpipe).
- The prototype edits the payload-label checks in the RAM overlay at boot; the
  real change edits pengyos-live-installer.service and console-installer.
- The live installer's welcome text says "running from the USB stick": wrong
  for a CD or a VM.

Still to do before it can ship: the build integration (kernel option, Buildroot
host packages, initramfs from Buildroot's busybox, post-image), the NVIDIA
editions (their modules), the release changes and boot check, and real hardware:
a stick `dd`'d from the ISO on behoweather (strict N100 firmware) and beholaptop.
