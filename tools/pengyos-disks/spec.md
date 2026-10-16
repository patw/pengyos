# pengyos-disks — "Disks", the Desktop's disk utility

## Summary
A libcosmic app for the Desktop that does the disk jobs people otherwise need a
terminal for: **see** every drive and how it is laid out, **partition** it (a new
partition table; create, format, grow and delete partitions), **mount** it, turn
swap on and off, and the removable-drive jobs: **format** a stick, **write a disk
image** to it (the PengyOS installer `.img.xz` among them), and **eject** it. All
the work is done by UDisks2 over D-Bus; the app never runs as root.

## Purpose
The Desktop ships UDisks2, polkit and the mkfs tools (FAT, exFAT, NTFS, ext4,
btrfs, XFS, swap), but nothing graphical drives them: COSMIC Files mounts and
ejects, and has no Format action; COSMIC has no Disks app. Adding a second hard
drive, or making an installer stick, needed `parted`, `mkfs` and `dd`.
(0.1.0 covered only removable drives; 0.2.0 is the GNOME-Disks-shaped whole.)

## Deliverable
A Rust crate `pengyos-disks` (MIT), built like `pengyos-updates`:
- `lib.rs` — std only, `cargo test`-able with no display and no D-Bus: the drive
  model from a UDisks2 `GetManagedObjects` snapshot, the layout (partitions and
  free space), what is locked and why, the filesystem choices with their name and
  size rules, the step plan for every action, image decompression, the copy loop
  (progress, periodic flush, cancel), and the user-facing error text.
- `udisks.rs` (feature `udisks`, zbus) — reads the snapshot and executes plans.
- `app.rs` (feature `cosmic` = `udisks` + libcosmic) — the window.

## Interface
- `pengyos-disks` — the window.
- `pengyos-disks FILE` — the window, with FILE chosen as the image to write
  (the `.desktop` file registers the disk-image MIME types, so COSMIC Files'
  *Open with* offers it).
- `pengyos-disks --list` (feature `udisks`) — every drive with its table,
  partitions, free space and locks; no window. For SSH and tests.

## Behaviour

**Which drives.** Every UDisks drive with media (an empty card reader is not
listed): internal first, then removable, each by device name. *Removable* means
`Drive.Removable`, `MediaRemovable`, `Ejectable`, or `ConnectionBus`
usb/sdio/mmc; removable drives also get *Format…* and *Eject*. UDisks' "no
object" path `/` is read as none.

**Layout.** The partitions outside an MBR extended partition, by offset, with the
free space between them: a gap starts at the next 1 MiB boundary after the
previous partition's end (UDisks aligns to 1 MiB; the first partition starts at
1 MiB) and ends at the usable end — the drive's size less GPT's 33-sector backup
table rounded down to 1 MiB, or for MBR never past 2 TiB. Gaps under 4 MiB are
alignment slack and not shown. Logical partitions are shown inside their
extended partition and cannot be created (MBR's extended layout is legacy).

**Locks.** A drive is locked for the whole-drive actions (new table, format,
write image) when any filesystem on it is mounted outside `/run/media/` and
`/media/` (the system's), any of it is active swap, or it is read-only. A
partition is locked for format/grow/delete/unmount when it, or the cleartext side
of it if it is an unlocked container, is mounted by the system or is active swap;
when it or its drive is read-only; when it is an extended partition with a locked
partition inside; and when it is one of the running PengyOS's own partitions —
GPT name `PENGYOS-ESP`, `pengyos-root-a`, `pengyos-root-b`, `pengyos-data`, or
filesystem label one of those or `PENGYOS-PAYLOAD` — **on a drive that is in use
by the system**. The inactive root slot is never mounted and is still locked; the
same names on another, unused disk (an old install) are not.
`80-pengyos-udisks-hide.rules` hides those partitions from file managers
(`UDISKS_IGNORE` → `HintIgnore`) but not from UDisks, so the app sees and locks
them.

**Filesystems.**
| Choice | mkfs | MBR type | GPT type | name limit | smallest |
|---|---|---|---|---|---|
| ext4 | ext4 | 0x83 | Linux filesystem | 16 bytes | 1 MiB |
| btrfs | btrfs | 0x83 | Linux filesystem | 255 bytes | 128 MiB |
| XFS | xfs | 0x83 | Linux filesystem | 12 bytes, no spaces | 300 MiB |
| FAT32 | vfat | 0x0c | basic data | 11 ASCII, stored in capitals | 8 MiB |
| exFAT | exfat | 0x07 | basic data | 11 UTF-16 units | 8 MiB |
| NTFS | ntfs | 0x07 | basic data | 32 UTF-16 units | 8 MiB |
| Swap | swap | 0x82 | Linux swap | 15 bytes | 1 MiB |

Names refuse control characters, and FAT32 also `"*/:<>?\|+,.;=[]`. An empty
name is allowed. The limits are libblockdev's or stricter, and the smallest sizes
are what mkfs accepts: both are checked before anything is written, because a
refusal after `CreatePartitionAndFormat` has made the partition leaves it behind
unformatted. ext4, btrfs and XFS are made with `take-ownership` (the user's, not
root's). Whole-drive *Format…* offers FAT32 (default), exFAT, NTFS and ext4.

**Plans** (each step is one UDisks call):
- *Format…* (removable drive): unmount everything mounted, lock unlocked
  containers → `Block.Format(table)` (MBR up to 2 TiB, GPT above) →
  `PartitionTable.CreatePartitionAndFormat(1 MiB, 0 = the rest, type, "", {}, fs,
  {label, take-ownership})`.
- *New partition table…*: teardown → `Block.Format("gpt" | "dos")`. MBR is
  refused above 2 TiB.
- *Create partition…*: in a gap the drive still has, a size (decimal units like
  the sizes shown, `GiB` and the like binary, a bare number is GB; rounded down to
  1 MiB; empty = all of the gap) no bigger than the gap and at least the
  filesystem's smallest → `CreatePartitionAndFormat(gap start, size, type, "",
  {}, fs, opts)`, or `CreatePartition` when left unformatted. Refused with no
  table, on a read-only drive, and on MBR with four primary partitions.
- *Format…* (partition): teardown of it (and what is inside it) →
  `Block.Format(fs, {label, take-ownership, update-partition-type})`.
- *Grow…*: only into free space directly after the partition →
  `Partition.Resize(new size)` (the end moves; the start never does) →
  `Filesystem.Resize(0 = fill)`. ext2/3/4 grow mounted or not; btrfs and XFS only
  mounted (refused with "Mount it first"); NTFS only unmounted (unmounted first);
  anything else is not offered. Shrinking is not offered.
- *Delete…*: teardown → `Partition.Delete({tear-down})`.
- *Mount* / *Unmount*: `Filesystem.Mount` / `Unmount` (a system mount is not
  the window's to unmount). *Turn swap on/off*: `Swapspace.Start` / `Stop`.
- *Write image…* and *Eject*: as 0.1.0 — teardown → `Block.OpenForRestore` → copy
  in 4 MiB blocks, flushing every 64 MiB → `Block.Rescan`; and teardown →
  `Drive.PowerOff` when `CanPowerOff`, else `Drive.Eject`.

Every plan is made again from a fresh snapshot when its button is pressed (the
window refreshes every 2 s; a dialog's drive may be stale).

**Write image** (unchanged from 0.1.0). `.xz`, `.zst`/`.zstd` and `.gz` are
decompressed on the fly with the image's own `xz`/`zstd`/`gzip`. Known image size
larger than the drive → refused before anything is touched (raw: file size;
`.xz`: `xz --robot --list`). Progress is bytes written over the unpacked size when
known, else image bytes read over the file size. Cancel stops at the next block and
says the drive is now incomplete; a device that fills up is "the image is bigger
than the drive".

**Confirmation.** Every destructive action names what it erases ("Everything on
SanDisk Cruzer Blade (32.0 GB) will be erased: KINGSTON (FAT, 31.9 GB)", "Photos
(ext4, 200.0 GB) (/dev/sda2) on … will be deleted"), with a red button; nothing is
touched before it is pressed. Growing is not destructive but says to keep the
power on.

**Privileges.** No polkit rules are added. With UDisks' shipped policy the active
local user formats, partitions, mounts and ejects removable drives freely; the
`-system` actions (an internal disk) and opening a device to write an image ask for
an administrator's password through COSMIC's polkit agent (per
`50-pengyos-admin.rules`, the user's own password for wheel members). A dismissed
prompt is "Cancelled", a refused one "Only an administrator of this computer can do
that."

## Verification
- `cargo test` (std only): the drive model from fixture snapshots shaped like
  UDisks' own (object paths of `/` for none): a FAT32 stick, the live installer
  stick, an installed PengyOS NVMe (ESP, root-a mounted at `/`, root-b unmounted,
  data), the same disk unused, a spare disk, an MBR disk with an extended
  partition, a read-only SD card, an empty card reader; the layout and free-space
  maths (alignment, GPT's tail, MBR's 2 TiB, slack); every lock; every plan and
  its refusals; name and size rules; size parsing; decompressor choice; `xz
  --robot --list`; the copy loop; error-text mapping.
- In QEMU (Desktop image, a `usb-storage` stick): format FAT32 / exFAT / NTFS /
  ext4 and mount each in COSMIC Files; write the PengyOS installer `.img.xz` and
  boot it; eject; `--list` refuses the live stick when booted from it.
- In QEMU (Desktop image, a second `virtio` disk): new GPT table; create, mount,
  grow and delete partitions; the system disk's partitions shown and locked.

**GitHub-ready contract**: README.md, LICENSE (MIT, "Copyright (c) 2026 Pat
Wendorf"), CHANGELOG.md, `.gitignore`, `verify.toml`, a green `cargo test`.

## Acceptance criteria
- A USB stick can be formatted to each of the four filesystems and written with
  a `.img`/`.img.xz` from the window, without a terminal.
- A second hard drive can be given a partition table and partitioned, formatted,
  mounted, grown and cleaned up again from the window, without a terminal.
- The running system's drive cannot be erased or written, and none of its
  PengyOS partitions (mounted or not) can be formatted, grown or deleted; nor can
  write-protected media.
- `cargo test` green with no display and no D-Bus.

## Non-goals
- Shrinking or moving partitions, creating logical (MBR extended) partitions,
  encryption (creating or unlocking LUKS), SMART, benchmarks, secure erase /
  zero-fill, editing partition types and flags by hand, disk images of a drive
  (backup). GParted territory, or later.
- Verifying an image's checksum (the download page publishes `SHA256SUMS`).
