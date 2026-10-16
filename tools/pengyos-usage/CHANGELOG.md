# Changelog — pengyos-usage

## [0.1.0] — 2026-10-16

### Added
- The Disk Usage app (libcosmic): Places (home and each filesystem, once, with how
  full it is) and *Scan a folder…*; a parallel, stoppable scan with live counts;
  the answer as a four-ring chart you click into beside a ranked list, with crumbs,
  *Open in Files* and *Scan again*. Disk usage (`st_blocks`), hard links once,
  symlinks not followed, other filesystems not walked (by mount point, so btrfs
  subvolumes are), btrfs `.snapshots` not counted, unreadable folders noted.
  `--report FOLDER` prints the same without a window.
