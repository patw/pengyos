# PengyOS initramfs — deterministic minimal initramfs builder

## Summary
Build a small, reproducible cpio initramfs from a config.

## Purpose
SPEC §1: a controlled early-userspace we own.

## Deliverable
A Python CLI `pengyos-initramfs` producing a deterministic (sorted, mtime-normalized) gzip cpio archive from a spec + a root dir.

## Interface
- pengyos-initramfs build --root DIR --spec initramfs.toml --out init.cpio.gz

## Spec format (initramfs.toml)

The spec is TOML (parsed with stdlib `tomllib`). It has four *include* lists —
paths **relative to `--root`** — plus optional `[build]` (determinism/normalization)
and `[init]` (the `/init` generator) tables:

```toml
# What to pack. Paths are relative to --root, must be relative POSIX paths
# (no leading '/', no '..'); a leading './' is stripped.
binaries    = ["bin/busybox"]                 # executables
files       = ["etc/os-release"]              # plain data files
modules     = ["lib/modules/6.1.0/loop.ko"]    # kernel modules
directories = ["etc"]                          # explicit directory entries

[build]                                       # all optional; defaults shown
mtime = "2000-01-01T00:00:00Z"                 # ISO-8601, normalized to UTC
uid = 0
gid = 0
user = "root"
group = "root"

[init]                                        # the generated /init entry
path = "init"                                  # archive-internal path
mode = "0755"                                  # octal string (or integer)
```

Validation rules (any violation is a `SpecError`, CLI exit code 4):
- include lists (including `directories`) must be arrays of strings;
- every path must be relative (no leading `/`), contain no `..`, no backslash,
  and be non-empty after normalization;
- `build.mtime` must be a valid ISO-8601 timestamp (naive ⇒ assumed UTC,
  offsets ⇒ converted to UTC); `uid`/`gid` must be integers in `[0, 2**32)`;
  `user`/`group` must be non-empty strings;
- `init.mode` must be an octal string or integer in `[0, 07777]`; `init.path`
  follows the same path rules.

## Archive format and determinism (discovered/recorded 2026-09-17)

The output is a **gzip** stream containing a single **newc** (`070701`) cpio
archive, as consumed by the Linux kernel's initramfs unpacker
(`init/initramfs.c`) and by `cpio -i`. Concretely:

* **Header** — 6-byte magic `070701` + 13 fields, each an 8-digit uppercase
  ASCII hex string (`ino mode uid gid nlink mtime filesize devmajor devminor
  rdevmajor rdevminor namesize check`) = **110 bytes**. (The "11-field/94-byte"
  variant floating around is wrong; the kernel parses `__be32 header[13]`
  and `CPIO_HDRLEN` is 110.)
* **Padding** — each entry is `header + name-region + data`, where the
  name-region is the NUL-terminated name padded to the kernel's
  `N_ALIGN(len) = (((len) + 1) & ~3) + 2` bytes and the data is NUL-padded to
  a multiple of 4. All padding bytes are zeros. The archive ends with a
  `TRAILER!!!` entry.
* **Order** — entries are written in the sorted order of `Spec.all_paths()`;
  inodes are the 1-based archive position (never filesystem inodes).
* **Ownership/mtime** — every entry uses the spec's normalized
  `uid`/`gid`/`mtime`. `newc` has no user/group *name* fields, so
  `[build].user`/`[build].group` are accepted and validated but do not affect
  the output bytes.
* **Modes are normalized per include kind**, not copied from the checkout
  (umask/checkout state must not leak into the artifact):
  `binaries` → `0o100755`, `files`/`modules` → `0o100644`, symlinks →
  `0o120777` (conventional), `directories` → `0o040755`. A path listed in
  several kinds is packed once; the first listing in the order directories,
  binaries, files, modules wins (`directories` first because it is the only
  kind that can represent a directory on disk).
* **Symlinks** — a spec path that is a symlink under `--root` is packed as a
  symlink entry whose data is the link target.
* **Gzip container** — MTIME header field = 0, no FNAME, compression level 9,
  so the `.cpio.gz` bytes are a pure function of (spec, file contents).
* The archive contains **exactly** the spec'd paths **plus the generated
  init entry**: no auto-added parent directories. Paths listed in
  `directories` are packed as **directory entries** (name stored with a
  trailing `/`, empty data, mode `0o040755`, normalized uid/gid/mtime like
  everything else); the kernel's initramfs populate creates them during
  extraction, which is what makes nested file paths (e.g. `etc/os-release`)
  extractable at boot, since the kernel does **not** create missing parent
  directories. A `directories` path that is missing under `--root` or is not
  a directory (a file, a broken symlink, ...) is a build error (exit code 5);
  a symlink *to a directory* is accepted and packed as a plain directory
  entry.
* **The generated `/init` entry** (recorded 2026-09-16) — every archive
  contains one entry at `[init].path` (default `init`), a *generated* minimal
  POSIX `sh` script (from `pengyos_initramfs/initgen.py`): `#!/bin/sh`
  shebang, `set -eu`, `mkdir -p /proc /sys /dev`, `mount` of `proc`/`sysfs`/
  `devtmpfs`, then `exec /bin/sh` (classic rescue-initramfs handoff). Its
  content is a pure function of the spec (no timestamps or
  environment-dependent values), its mode is `0o100000 | [init].mode`
  (default `0o100755`), and it sits in its canonical **sorted** position
  among the other entries (inodes are 1-based archive positions, so it
  shifts the others). It does not need to exist under `--root`. If the
  operator *explicitly* lists `init.path` in an include list, the real file
  from the root wins (packed once, with the include kind's normalized mode).
* A spec path that is missing under `--root` or is a directory is a build
  error (distinct exit code below).

## CLI exit codes

| code | meaning |
|------|---------|
| 0 | success |
| 2 | usage / argument error (missing `--root` dir, missing `--spec` file, argparse errors) |
| 4 | spec file failed to parse/validate (`SpecError`) |
| 5 | spec valid, but building failed (`BuildError`: missing file under root, directory where a file is expected, non-directory where a `directories` entry is expected, unwritable `--out`) |

## Verification (tests are first-class)
- two builds are byte-identical (determinism).
- archive contains exactly the spec'd files; no extras.
- init is executable and syntactically valid sh.
- opt-in integration tests (`PENGYOS_IT=1`, skipped when the system `cpio`
  binary is absent) verify the writer against a real independent newc
  implementation (`cpio -t` / `cpio -idm`).

**GitHub-ready contract** (assert with a `test_github_ready`):
README.md, LICENSE (MIT, "Copyright (c) 2026 Pat Wendorf"), CHANGELOG.md,
`.gitignore`, and a full green test suite. Docs are authored — keep them in sync.

## Acceptance criteria
- Bit-reproducible output.
- `pytest -q` green; test_github_ready green.

## Non-goals
- Being dracut

## Constraints
- Runs as a non-root user with no network at test time. Mock external tools; use
  committed fixtures. Real system integration is opt-in via `PENGYOS_IT=1`.
