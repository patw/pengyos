# pengyos-usage — "Disk Usage", what is taking the space

## Summary
A libcosmic app for the Desktop that answers "where did my space go" as a
picture: scan a folder or a filesystem, then show it as a ring chart you can click
into, beside a ranked list. Read-only.

## Purpose
`du -sh * | sort -h` answers it a line at a time, if you know where to look.
COSMIC has no disk usage analyzer. This is one, a native window like the
Desktop's other tools (Disks, Updates, Printers).

## Deliverable
A Rust crate `pengyos-usage` (MIT), built like `pengyos-disks`:
- `lib.rs` — std + libc, `cargo test`-able with no display: the scan, the
  mounts and filesystems, sizes and words, the chart's geometry and hit testing.
- `app.rs` (feature `cosmic`) — the window.

## Interface
- `pengyos-usage` — the window, at Places.
- `pengyos-usage FOLDER` — the window, scanning FOLDER.
- `pengyos-usage --report FOLDER` — no window: total, then the biggest entries
  three levels deep with sizes and shares, and the notes.

## Behaviour

**Places.** The home folder (`$HOME`) first, then each filesystem from
`/proc/self/mountinfo` of a type that holds files (ext2/3/4, btrfs, xfs, vfat,
exfat, ntfs, ntfs3, fuseblk, f2fs, bcachefs), not under `/proc`, `/sys`, `/dev`,
`/snap` or `/run` (except `/run/media`), each device once: at its top mount (the
one whose mountinfo root is `/`), else its shortest path. Each with used, size and
free from `statvfs` (free = what an ordinary user can write). `/` is "This
computer's system"; a removable drive under `/run/media/<user>/` by its name.
*Scan a folder…* opens the portal's folder chooser.

**The scan.** From the chosen folder, not following symlinks (`DirEntry::metadata`
does not traverse them); size is `st_blocks × 512`; a file with more than one link
counts once per (device, inode); a folder that is a mount point of anything
mounted strictly inside the scanned folder is not entered; a folder called
`.snapshots` on btrfs (`statfs` magic `0x9123683E`) is not entered; a folder that
cannot be read is counted (the first 50 kept by path) and the scan goes on. The
folders directly inside the scanned one are walked by up to 8 threads. Counts
(items, bytes) are shared with the window, which shows them four times a second;
Stop sets a flag every walker checks per entry, and a stopped scan shows nothing.
Each folder keeps its 100 biggest entries by name (biggest first, ties by name);
the rest are summed into one "N smaller items" entry.

**The chart.** Centre: the folder shown, with its name and size (or, under the
pointer, the hovered entry's name, size and share). Ring 1: its entries, each an
arc as long as its share of the centre's size, clockwise from twelve o'clock,
biggest first; rings 2–4: the same for the folders in ring 1, nested under their
parent's arc. Slices thinner than 0.3% of a turn are not drawn. One hue per slice
of ring 1, lighter outwards; files a little greyer; summed entries grey. Clicking
a folder slice makes it the centre; clicking the centre goes up one folder.

**The list.** The centre's entries, biggest first: name (folders end in `/`),
size, a bar and a share of the centre. Folders are clickable. The hovered slice's
top-level entry is shown bold.

**Around them.** Crumbs from the scanned folder to the centre (each clickable),
*Places*, *Open in Files* (`xdg-open` on the folder shown), *Scan again*. Notes:
the real filesystems not walked, snapshots not counted, folders not readable.

## Verification
- `cargo test`: sizes against the files' own `st_blocks` in a real temporary tree;
  a hard link counted once; a symlink's own size, not its target's; a skipped
  mount noted and not walked; an unreadable folder counted and the rest scanned
  (skipped as root); cancel; the 100-entry fold; mountinfo parsing and unescaping;
  one entry per filesystem; mounts inside a scan; the chart's nesting, proportions
  and hit testing; words.
- `--report` against a real tree agrees with `du -s -B1`.
- The window: hover, click in, centre to go up, crumbs, Stop, Places, on a Desktop
  image in QEMU.

**GitHub-ready contract**: README.md, LICENSE (MIT, "Copyright (c) 2026 Pat
Wendorf"), CHANGELOG.md, `.gitignore`, `verify.toml`, a green `cargo test`.

## Acceptance criteria
- From the App Library, without a terminal, a person can find which folders and
  files take the most space in their home folder or on any drive, and open that
  folder in Files.
- The total for a folder equals `du -s -B1`'s, a scan never leaves the
  filesystem it started on, and nothing on disk is changed.
- `cargo test` green with no display.

## Non-goals
- Deleting, moving or cleaning anything (Files does that; a cleanup tool would be
  a separate, reviewed thing).
- Scanning as root, or another user's files.
- Apparent size (`du --apparent-size`), btrfs exclusive/shared accounting
  (qgroups), per-user totals, history over time, a treemap view.
