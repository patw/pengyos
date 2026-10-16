# Changelog

All notable changes to **pengyos-storage** are documented in this file.

The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing outstanding._

## [0.1.0] - 2026-10-15

First release. Agent-authored (Pengy, on behoserver) for the PengyOS toolkit;
the request came from the operator ("a `du | sort -n`, but visual").

### Added

- **`scan`** — walk a tree and report it ranked: a bar-charted terminal report
  per depth, a **self-contained HTML page** (inline CSS + inline SVG: a donut
  pie, a squarified treemap where area is bytes, ranked tables, the top
  individual files), or a JSON document (schema 1).
- **`du`** — `du` output sorted biggest-first with share bars, `-a` for
  individual files, `--no-bar` for plain output. The `du | sort -n`
  replacement.
- **`disks`** — every mounted filesystem with a used-space bar, over
  `/proc/mounts` + `shutil.disk_usage`. Pseudo filesystems are skipped;
  network mounts are skipped unless `--network` asks for them, because
  `statvfs` on a dead NFS mount blocks.
- **Honest measurement**: disk usage (`st_blocks * 512`) by default with
  `--apparent` for `st_size`; hard links counted once (the `du` rule, so a
  snapshot-heavy btrfs `data` partition does not report double); symlinks never
  followed; `--one-file-system` reports a mount point instead of walking it;
  `--exclude` space is reported as excluded, never silently dropped.
- **Warnings, not crashes**: an unreadable directory is listed in `warnings`
  and the scan finishes with exit 0.
- **Deterministic output**: children sort by size descending then name
  ascending, so two scans of an unchanged tree are byte-identical.
- **Injectable walker** (`scandir`/`stat` parameters) so the suite drives hard
  links, foreign devices and permission errors from a synthetic tree — no root,
  no mounts, no network.
- Standard library only; read-only; no config file, no daemon, no root.

### Notes

- The HTML page is deliberately dependency-free: a chart that needs a CDN is
  not a report. `tests/test_html.py` asserts no `http(s)://`, no `<link>`,
  `<img>`, `<iframe>`, `<script>` and no inline event handlers.
- Geometry is tested, not eyeballed: pie slices sum to 360° with the correct
  large-arc flag; treemap rectangles stay in the canvas, never overlap, cover
  it, and have areas proportional to bytes (±1%).
