# Changelog — pengyos-disks

## [0.1.0] — 2026-10-11

### Added
- The Disks app (libcosmic, over UDisks2): format a removable drive (FAT32,
  exFAT, NTFS, ext4, with a name), write an `.img`/`.iso` or compressed
  `.img.xz`/`.zst`/`.gz` image to it with progress and Stop, and eject it.
  Drives in use by the system, with active swap, or write-protected are locked;
  internal disks are not shown. `--list` prints the same without a window.
  Needs glibc's UTF-16 gconv module in the image for exFAT names (the desktop
  layer copies it).
