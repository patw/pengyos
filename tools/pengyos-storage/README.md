# pengyos-storage

**See where the disk went.** A read-only storage visualizer: it walks a tree
the way `du` does and shows the answer as a **sorted, bar-charted terminal
report** and a **self-contained HTML page with a pie chart and a treemap**.

`du -sh * | sort -n` answers "what is big" in a form you have to read line by
line. "Where did my 900 GB go" is a *visual* question — the answer is a shape.
This tool is the answering tool, and the JSON document is the evidence an agent
can cite before proposing a cleanup.

- **Read-only.** It never deletes, moves, compresses or touches a byte.
  `pengyos-disks` owns the destructive disk work.
- **Standard library only.** No dependencies, no network, no root, no daemon.
- **Honest numbers.** Disk usage (`st_blocks*512`), not apparent size; hard
  links counted once; symlinks never followed; excluded space reported as
  excluded, never dropped.

## Install

```bash
pip install -e .              # editable dev install (no runtime deps)
python3 -m pengyos_storage    # or run it straight from the source tree
```

## Usage

```bash
# Every filesystem, biggest first, with a used-space bar.
pengyos-storage disks

# The visualizer: a pie + treemap page you can open from a USB stick.
pengyos-storage scan /data -f html -o storage.html

# The terminal report: ranked, bar-charted, per depth.
pengyos-storage scan /data -d 2

# du, sorted biggest-first, with share bars — the `du | sort -n` replacement.
pengyos-storage du /data -d 2

# Machine-readable, for an agent or a script.
pengyos-storage --json scan /data
pengyos-storage scan /data --json
```

### `scan`

| option | meaning |
|---|---|
| `-f text\|html\|json` | output format (default `text`) |
| `-o FILE` | write the document to a file |
| `-d N` | how deep to report (default 3); deeper space is summed into the node that stops |
| `-n N` | entries per level, and slices per chart (default 15) |
| `--min-size SIZE` | hide entries below this size (`10M`, `1.5G`); totals stay exact |
| `--apparent` | report apparent size (`st_size`) instead of disk usage |
| `-x` | do not descend into directories on other filesystems |
| `--exclude PATTERN` | `fnmatch` pattern to skip, repeatable (`*.iso`, `cache/*`) |
| `--json` | machine-readable output — accepted **before or after** the subcommand |

### `du`

`-d N` levels (default 1), `-a` to include individual files, `--no-bar` for
plain output, plus the same `--apparent` / `-x` / `--exclude` / `--min-size` /
`--json` flags.

### `disks`

`-f text|html|json`, `-o FILE`, `--network` (include NFS/CIFS mounts —
measuring a dead mount can block, so they are skipped by default),
`--mounts FILE` (default `/proc/mounts`).

## What the report guarantees

- **Biggest first, always.** Every table, every chart, every `du` line.
- **The arithmetic closes.** `total = listed + below-the-cut + excluded`, and
  each of those is printed. Nothing vanishes between the scan and the report.
- **The page is self-contained.** Inline CSS, inline SVG, no JavaScript, no
  network references, no fonts, no images — it renders identically offline.
- **Deterministic.** Two scans of an unchanged tree produce identical output.
- **Warnings, not crashes.** An unreadable directory is listed in `warnings`
  and the scan finishes.

## JSON document (schema 1)

```json
{
  "tool": "pengyos-storage", "version": "0.1.0", "schema": 1,
  "root": "/data", "mode": "blocks", "generated": "2026-10-15T12:00:00Z",
  "total_bytes": 507857, "file_count": 12, "dir_count": 3,
  "excluded_bytes": 0, "warnings": [],
  "filesystem": {"device": "/dev/nvme1n1p1", "mountpoint": "/data",
                 "fstype": "ext4", "size": 982, "used": 507, "free": 475},
  "tree": {"path": "/data", "relpath": ".", "name": "data", "bytes": 507857,
           "children": [], "files_here": [{"name": "big.iso", "bytes": 100}]}
}
```

Scanning more than one path at once wraps the documents in
`{"tool","version","schema","generated","scans":[ … ]}`.

## Testing

```bash
python3 -m pytest -q
```

The suite needs no network, no root and no real disks: the walker takes an
injectable `scandir`, so hard links, symlinks, foreign-device directories and
permission errors are driven from a synthetic tree. The repo-level gate runs
this suite too:

```bash
python3 scripts/pengyos_gate.py run --repo . --mode full
```

## Limits

Apparent size and disk usage differ for sparse, compressed and
hard-linked files — that is the point of measuring blocks, and the reason
`--apparent` exists. btrfs qgroup/exact-compression accounting, per-user
quotas and ACLs are out of scope (see `spec.md` → *Non-goals*).
