# pengyos-storage — see where the disk went

## Summary
A read-only storage visualizer: walk a tree the way `du` does, and show the
result as a **sorted, bar-charted terminal report** and a **self-contained HTML
page with a pie chart and a treemap**.

## Purpose
`du -sh * | sort -n` answers "what is big" only if you already know where to
look, and it answers it in a form you have to read line by line. Answering
"where did my 900 GB go" is a *visual* question: the answer is a shape, not a
list. This tool is the answering tool — the Server/Desktop comfort tool for
disk space, in the same family as `pengyos-disks` (which manages drives, not
usage).

It is also the tool an agent should call before proposing any cleanup: the
JSON document is the evidence, the HTML page is the report.

## Deliverable
A Python CLI `pengyos-storage`, **standard library only**, no network, no root,
no daemon:

```
pengyos-storage disks                       # every filesystem, with a used-space bar
pengyos-storage scan  [PATH] -o report.html # pie + treemap + tables (HTML)
pengyos-storage du     [PATH] -d 2          # du, sorted biggest-first, with bars
```

## Interface

### `scan` — the visualizer

```
pengyos-storage scan [PATH ...] [--format text|html|json] [-o FILE]
                     [--depth N] [--top N] [--min-size SIZE]
                     [--apparent] [--one-file-system] [--exclude PATTERN]...
                     [--json]
```

| option | default | meaning |
|---|---|---|
| `PATH ...` | `.` | one or more trees to scan; each is reported separately |
| `--format` | `text` | `text` (bars on stdout), `html` (self-contained page), `json` |
| `-o, --out` | stdout | write the document to a file (required for `html` in practice) |
| `--depth N` | 3 | how deep to descend for the report; deeper sizes are still summed into the node that stops at the limit |
| `--top N` | 15 | how many entries each level lists |
| `--min-size SIZE` | 0 | hide entries smaller than this from the report (they stay in the totals) |
| `--apparent` | off | report apparent size (`st_size`) instead of disk usage (`st_blocks*512`) |
| `-x, --one-file-system` | off | do not descend into a directory on another device (mount points are reported, not walked) |
| `--exclude PATTERN` | — | `fnmatch` pattern against the path relative to the scan root; repeatable. Excluded space is reported as `excluded`, never silently dropped |
| `--json` | off | machine-readable document (accepted before **or** after the subcommand) |

### `du` — the sorted `du`

```
pengyos-storage du [PATH ...] [-d N] [-a] [-h] [--apparent] [-x] [--exclude PATTERN]... [--json]
```

Prints one line per directory (and per file with `-a`), **sorted biggest
first**, each with a bar scaled to the largest entry. This is the
`du | sort -n` replacement: same numbers, already sorted, already ranked.

### `disks` — the filesystem view

```
pengyos-storage disks [--json]
```

One line per mounted filesystem: device, mount point, size, used, free, and a
used-space bar. Uses `shutil.disk_usage`, so it needs no root and reads no
privileged source.

## Measurement rules (the numbers must be honest)

1. **Disk usage, not apparent size, by default.** `st_blocks * 512` — what the
   file costs the filesystem, so a sparse file and a 4 KiB-block file report
   what they actually occupy. `--apparent` opts into `st_size`.
2. **Hard links are counted once.** A file with `nlink > 1` contributes its
   space at the first place it is seen and 0 afterwards (the `du` rule).
   Without this, a snapshot-heavy btrfs `data` partition reports double.
3. **Symlinks are never followed** (no cycles, no surprise `/proc`). A symlink
   contributes its own size.
4. **Mount points are visible.** `-x` stops at them; without `-x` the walker
   descends and the report marks the node as a mount point.
5. **Unreadable directories are warnings, not failures.** The scan finishes,
   the path is listed in `warnings`, and the exit code stays 0.
6. **Nothing is dropped silently.** `total = Σ node bytes`, and the report
   shows `excluded` and `hidden (< --min-size)` alongside the total, so the
   arithmetic closes.
7. **Deterministic.** Children sort by size descending, then name ascending.
   Two scans of an unchanged tree produce byte-identical text/JSON (the
   `generated` timestamp is the only field that changes).

## The visual output

`--format html` writes **one self-contained file**: inline CSS, inline SVG,
**no JavaScript, no network references, no fonts, no images**. It must render
identically from a USB stick, an SSH-tunnelled browser, or a `file://` URL on a
machine with no internet.

| block | content |
|---|---|
| header | root, total, mode (blocks/apparent), scan time, filesystem usage bar |
| **pie** | donut of the top `--top` entries at depth 1, remainder merged into `Other`, total in the middle, legend with size + percent |
| **treemap** | squarified treemap of the same entries: area = bytes, so a 40 % slice is a 40 % rectangle |
| tables | top entries per depth level (with bars), top individual files, warnings |

Geometry rules, asserted by tests:

* pie slices sum to exactly 360°; a slice ≥ 50 % uses the large-arc flag;
* treemap rectangles are inside the canvas, do not overlap, and their areas
  are proportional to their values (±1 % rounding);
* an entry is labelled only if its rectangle is wide/tall enough to hold the
  text (no clipped labels, no overlapping text).

## JSON document (schema 1)

```json
{
  "tool": "pengyos-storage", "version": "0.1.0", "schema": 1,
  "root": "/data", "mode": "blocks", "generated": "2026-10-15T12:00:00Z",
  "total_bytes": 123, "file_count": 12, "dir_count": 3,
  "excluded_bytes": 0, "warnings": ["permission denied: /data/x"],
  "filesystem": {"device": "/dev/nvme1n1p1", "mount": "/data",
                 "size": 1e11, "used": 5e10, "free": 5e10},
  "tree": {"path": "/data", "relpath": ".", "name": "data",
           "bytes": 123, "files": 12, "dirs": 3,
           "mount": false, "children": [ ... ], "files_here": [
               {"name": "big.iso", "bytes": 100} ]}
}
```

`disks --json` emits `{"tool","version","schema":1,"filesystems":[{device,
mountpoint,size,used,free,used_pct}]}`.

## Verification (tests are first-class)

* **Synthetic tree, injected `scandir`** — the walker takes an injectable
  directory-listing callable, so the suite drives hard links, symlinks,
  foreign-device directories, permission errors and exclusions **offline,
  without root and without touching real disks**.
* Block rounding: usage ≥ apparent, and usage is a multiple of 512.
* Hard link counted once; excluded subtree reported as excluded, totals close.
* Geometry: pie angles sum to 360°; treemap rects non-overlapping, in-bounds,
  area-proportional.
* HTML: contains the SVG, contains no `http://`/`https://`/`src=` external
  reference, no `<script>`.
* CLI: exit codes (0 ok, 2 bad path/usage), `--json` in both argument
  positions, `-o` writes the file, text output is deterministic.
* `test_github_ready`: the contract files below, and the README documents the
  subcommands the CLI actually advertises.

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria

- `python3 -m pytest -q` green in this directory; `test_github_ready` green.
- `pengyos-storage scan /data -f html -o /tmp/x.html` produces a page that
  opens with no network and shows a pie, a treemap and the top-file table.
- `pengyos-storage du /data -d 2` output is sorted descending by size.
- `pengyos-storage scan <tree> --json` twice on an unchanged tree is
  byte-identical apart from `generated`.
- No third-party import anywhere in the package (stdlib only).

## Non-goals

- Deleting, moving, compressing or archiving anything. **This tool is
  read-only**; cleanup is a human decision (`pengyos-disks` owns the destructive
  disk work).
- A GUI app. The Desktop-facing surface is the HTML report; a libcosmic shell
  over `scan --json` is a separate, later component.
- Network filesystems smarter than "it is a mount point" (NFS/CIFS are just
  devices to the walker).
- Per-user quotas, ACLs, btrfs qgroup/exact-compression accounting.

## Constraints

- Standard library only (`os`, `shutil`, `argparse`, `json`, `math`,
  `fnmatch`, `datetime`, `html`). Declared in `pyproject.toml` if that ever
  changes.
- No root, no network, no daemon, no config file. Reads only what the invoking
  user can read.
- Bounded memory: the tree is kept in RAM, so `--depth` bounds the report and
  per-directory file lists are capped at `--top` entries.
- A scan never writes inside the scanned tree; output goes to `-o` or stdout.
