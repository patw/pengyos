# Changelog — pengyos-disks

## [0.2.0] — 2026-10-16

### Added
- **Every drive, partitioned.** Disks grows from the USB-stick tool into the
  Desktop's disk utility. Every drive (internal, external, USB, card) is listed
  down the side; the chosen one shows its partition table and a bar of its
  partitions and free space in proportion (1 MiB-aligned, GPT's backup table and
  MBR's 2 TiB limit allowed for, MBR extended partitions shown with their logical
  ones). New actions: *New partition table…* (GPT / MBR), *Create partition…* in
  free space (a size or all of it; ext4, btrfs, XFS, FAT32, exFAT, NTFS, swap, or
  unformatted; a name), *Format…* a partition, *Grow…* a partition and its
  filesystem into the space after it, *Delete…*, *Mount* / *Unmount*, *Turn swap
  on/off*. Sizes, names and each filesystem's smallest size are checked before
  anything is written. `--list` prints the layout and the locks.
- **The running system is locked.** Whole-drive actions are refused on a drive the
  system has something mounted from or swapping on; partition actions on a
  system mount, active swap, read-only media, and the running PengyOS's own
  partitions (`PENGYOS-ESP`, `pengyos-root-a/b`, `pengyos-data`, the stick's
  `PENGYOS-PAYLOAD`) on its own drive, mounted or not — the inactive root slot
  never is. The same partitions on another, unused disk can be erased.

### Changed
- The window has a sidebar of drives and a 980×680 default size; the stick flows
  (format, write an image, eject) are unchanged, and are now offered on any drive
  that is not locked (format and eject only on removable ones).

## [0.1.0] — 2026-10-11

### Added
- The Disks app (libcosmic, over UDisks2): format a removable drive (FAT32,
  exFAT, NTFS, ext4, with a name), write an `.img`/`.iso` or compressed
  `.img.xz`/`.zst`/`.gz` image to it with progress and Stop, and eject it.
  Drives in use by the system, with active swap, or write-protected are locked;
  internal disks are not shown. `--list` prints the same without a window.
  Needs glibc's UTF-16 gconv module in the image for exFAT names (the desktop
  layer copies it).
