# pengyos-disks — "Disks", format and write removable drives

## Summary
A small libcosmic app for the Desktop that does the three things people need to
do with a USB stick or memory card and could only do from a terminal: **format**
it (one partition, a filesystem and a name), **write a disk image** to it (an
`.img`/`.iso`, or the `.img.xz` PengyOS publishes, to make an installer stick),
and **eject** it. All the work is done by UDisks2 over D-Bus; the app never runs
as root.

## Purpose
The Desktop ships UDisks2, polkit and the mkfs tools (FAT, exFAT, NTFS, ext4),
but nothing graphical drives them: COSMIC Files mounts and ejects, and has no
Format action; COSMIC has no Disks app. Making a PengyOS installer stick from a
running PengyOS needed `dd`.

## Deliverable
A Rust crate `pengyos-disks` (MIT), built like `pengyos-updates`:
- `lib.rs` — std only, `cargo test`-able with no display and no D-Bus: the drive
  model built from a UDisks2 `GetManagedObjects` snapshot, which drives are
  offered and why others are not, the filesystem choices and name rules, the
  step plan for format / write / eject, image decompression choice, the copy
  loop (progress, periodic flush, cancel), and the user-facing error text.
- `udisks.rs` (feature `udisks`, zbus) — reads the snapshot and executes plans.
- `app.rs` (feature `cosmic` = `udisks` + libcosmic) — the window.

## Interface
- `pengyos-disks` — the window.
- `pengyos-disks FILE` — the window, with FILE chosen as the image to write
  (the `.desktop` file registers the disk-image MIME types, so COSMIC Files'
  *Open with* offers it).
- `pengyos-disks --list` (feature `udisks`) — print the drives the app would
  offer and the ones it refuses, with the reason; no window. For SSH and tests.

## Behaviour
**Which drives.** Only drives that are removable or on USB/SD/MMC
(`Drive.Removable`, `Drive.MediaRemovable`, `Drive.Ejectable`, or
`ConnectionBus` usb/sdio/mmc) with media present. A drive is listed but
locked, with the reason shown, when:
- any of its filesystems is mounted outside `/run/media/` and `/media/` (it is
  in use by the system — this covers the live installer stick a session runs from);
- it holds active swap;
- it is read-only (write-protect switch).
Internal disks are never shown (non-goal; the installer owns them).

**Format.** Choices, each with a one-line explanation:
| Choice | mkfs | MBR type | GPT type | name limit |
|---|---|---|---|---|
| FAT32 (default) | vfat | 0x0c | basic data | 11 ASCII chars, stored in capitals |
| exFAT | exfat | 0x07 | basic data | 11 chars (UTF-16 units) |
| NTFS | ntfs | 0x07 | basic data | 32 chars |
| ext4 | ext4 | 0x83 | Linux filesystem | 16 bytes |

Names refuse control characters, and FAT32 also `"*/:<>?\|+,.;=[]`. An empty name
is allowed. The limits are libblockdev's (UDisks checks a label only after the
table is rewritten, so the app must refuse first), or stricter. The table is MBR (`dos`) for drives up to 2 TiB and GPT above.
Plan: unmount each mounted filesystem → `Block.Format(table)` on the whole
drive → `PartitionTable.CreatePartitionAndFormat(1 MiB, 0 = the rest, type, "", {}, fs,
{label, take-ownership for ext4})`.

**Write image.** Pick a file (file chooser portal, or the command line).
`.xz`, `.zst`/`.zstd` and `.gz` are decompressed on the fly with the image's own
`xz`/`zstd`/`gzip`; anything else is written as is. Known image size larger than
the drive → refused before anything is touched (raw: file size; `.xz`:
`xz --robot --list`). Plan: unmount → `Block.OpenForRestore` → copy in 4 MiB
blocks, flushing to the device every 64 MiB so progress tracks the stick and not
the page cache → final flush → `Block.Rescan`. Progress is bytes written over
the unpacked size when that is known (raw, `.xz`), else image bytes read over the
file size (compressed bytes track written ones badly: zeros compress to nothing). Cancel stops at the next block and says the drive is now
incomplete. A device that fills up is reported as "the image is bigger than the drive".

**Eject.** Unmount → `Drive.PowerOff` when `CanPowerOff`, else `Drive.Eject`.

**Confirmation.** Format and write name the drive, its size and what is on it
("Everything on SanDisk Cruzer Blade (32.0 GB) will be erased: KINGSTON (FAT,
31.9 GB)"), with a
destructive button; nothing is touched before it is pressed.

**Privileges.** No polkit rules are added. With UDisks' shipped policy,
formatting (ext4's `take-ownership` included: UDisks covers it with
`modify-device` on a fresh format) and ejecting a non-system drive is allowed
for the active local user; opening the device to write an image (`open-device`)
asks for an administrator's password through COSMIC's polkit agent (which, per `50-pengyos-admin.rules`, is the user's own password for
wheel members). A dismissed prompt is "Cancelled", a refused one "Only an
administrator of this computer can do that."

## Verification
- `cargo test` (std only): drive model from fixture snapshots (a FAT32 stick, a
  live installer stick mounted at `/run/pengyos/payload`, an internal NVMe, a
  read-only SD card, an empty card reader); every format plan and name rule;
  table choice at the 2 TiB edge; decompressor choice; `xz --robot --list`
  parsing; the copy loop (progress, flush cadence, cancel, short device);
  error-text mapping.
- In QEMU (Desktop image, a `usb-storage` stick): format FAT32 / exFAT / NTFS /
  ext4 and mount each in COSMIC Files; write the PengyOS installer `.img.xz` and
  boot it; eject; `--list` refuses the live stick when booted from it.

**GitHub-ready contract**: README.md, LICENSE (MIT, "Copyright (c) 2026 Pat
Wendorf"), CHANGELOG.md, `.gitignore`, `verify.toml`, a green `cargo test`.

## Acceptance criteria
- A USB stick can be formatted to each of the four filesystems and written with
  a `.img`/`.img.xz` from the window, without a terminal.
- Mounted system drives, internal disks and write-protected media cannot be
  chosen for format or write.
- `cargo test` green with no display and no D-Bus.

## Non-goals
- Internal disks, resizing, multiple partitions, encryption (LUKS), SMART,
  secure erase / zero-fill, partition editing. GNOME Disks / GParted territory.
- Verifying an image's checksum (the download page publishes `SHA256SUMS`).
