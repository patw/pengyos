# pengyos-usage — "Disk Usage"

**What is taking the space?** A Desktop app that scans a folder or a whole
filesystem and shows the answer as a picture: a ring chart with the folder in the
middle, what is in it in the first ring, what is in those in the next, and so on,
each slice as big as the space it takes. Beside it, the same contents as a ranked
list with sizes and shares. Click a folder, in the chart or the list, to go into
it; click the centre, or a crumb at the top, to go back up. GNOME's *Disk Usage
Analyzer* (baobab), for COSMIC.

- **Places**: your home folder and each filesystem, with how full it is, and
  *Scan a folder…* for anything else. Each filesystem is listed once (PengyOS
  binds `/data`'s subvolumes on `/home`, `/var` and `/root`; they show as `/data`).
- **The scan** counts as it goes and can be stopped. The folders in the scanned
  one are walked in parallel; a home folder takes seconds, 12 million files on
  NVMe about two minutes.
- **Read-only.** Nothing is changed or deleted; *Open in Files* opens the folder
  on screen in COSMIC Files, where you decide.

## The numbers are the disk's

- **Disk usage**, not file length: `st_blocks × 512`, as `du` counts. A sparse
  VM image counts what it really holds.
- **Hard-linked files count once.**
- **Symlinks are never followed.**
- **Other filesystems are not walked.** A scan of `/` stops at `/data`, `/home`,
  `/boot/efi`, `/proc`… (mount points read from `/proc/self/mountinfo`, not
  device numbers, since every btrfs subvolume has its own) and names the real
  filesystems it left out.
- **btrfs snapshots are not counted.** A `.snapshots` folder on btrfs holds
  snapshots that share their blocks with what they copy; counting them would add
  the same space again. They are named in a note instead.
- **Folders it cannot read** (no permission) are counted and named, and the scan
  goes on. Run it on your own files; it never asks for a password.
- A folder with more than 100 entries keeps the 100 biggest by name and sums the
  rest into one "N smaller items" slice, so a folder of a million files costs one
  entry, and memory stays small.

## Use

```console
pengyos-usage                     # the window, at Places
pengyos-usage ~/Videos            # the window, scanning that folder
pengyos-usage --report /data      # no window: the biggest entries, three levels deep
```

## How it works

- `src/lib.rs` — std + libc only, `cargo test`-able with no display: the scanner
  (into a tree of [`Node`]s), the mounts and filesystems (`mountinfo`,
  `statvfs`), and the ring chart's geometry and hit testing.
- `src/app.rs` (feature `cosmic`) — the window; the chart is an iced canvas.

## Tested

`cargo test` (12 tests: real temporary trees for sizes, hard links, symlinks,
unreadable folders, skipped mounts and cancel; mountinfo parsing; the chart's
slices and hit testing). `--report` against this build host matches `du -s -B1`
to the byte (12,013,486,080 bytes for `tools/`); a 12.4-million-file `/data` in
2 m 44 s. The window driven under Xvfb (hover, click in, click the centre to go
up, Stop) and in QEMU on the Desktop image (Places, a scan of `/`).

## Build

```console
cargo test
cargo run -- --report ~                  # no window
CARGO_TARGET_DIR=$PENGYOS_COSMIC_BUILD/install-gui cargo build --release --features cosmic
```

`scripts/build-cosmic.sh pengyos-usage` does the last one; the Desktop
post-build installs it, and the overlay carries `io.catbee.pengyos.DiskUsage.desktop`.

On a Server, or in a script, the terminal answer is `du -h --max-depth=1 | sort -h`.
