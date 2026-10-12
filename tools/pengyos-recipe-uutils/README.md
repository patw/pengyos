# PengyOS Recipe — uutils/coreutils

Buildroot recipe for [uutils/coreutils](https://github.com/uutils/coreutils) —
the Rust reimplementation of GNU Coreutils — for [PengyOS](../../README.md).

## Purpose

PengyOS owns its recipes. uutils is the *Core* coreutils for PengyOS, so this
task authors our own Buildroot recipe + pinned hash for it rather than relying
on an upstream or third-party copy.

## Recipe layout

```
package/uutils-coreutils/
├── Config.in              # Kconfig entry (BR2_UUTILS_COREUTILS)
├── uutils-coreutils.mk    # Buildroot build recipe: pinned VERSION, LICENSE, install + symlink plan
└── uutils-coreutils.hash  # sha256 of the pinned source tarball
```

The recipe pins an exact upstream version (never `HEAD`), declares the MIT
license, builds the multicall `coreutils` binary with
`--no-default-features --features unix` (upstream's `feat_os_unix`: the 78
common-core utilities **plus** the unix-only ones GNU coreutils ships on Linux -
stat, id, sync, uname, whoami, install, stty, timeout, hostname, groups, nice,
nohup, chroot, chgrp, chmod, chown, nproc, mkfifo, mknod, logname, hostid, users,
who, uptime, pinky, stdbuf, kill, arch), and installs the 106 utility symlinks
(`ls`, `cat`, `mkdir`, …) alongside it. Building the *default* feature set would
silently drop `stat`, `id`, `sync`, `uname`, `whoami`, `install`, `stty` and
`timeout` from the image.

The copy the build uses lives in `buildroot-external/package/uutils-coreutils/`;
this tool owns it, and a test fails if the two copies drift.

## Development

The recipe rules are enforced by a Python test suite. Tests need no network,
root, display, or real hardware.

```bash
python3 -m pytest -q
```

Real-system integration tests (against a live Buildroot tree) are opt-in and
gated behind the `PENGYOS_IT=1` environment variable:

```bash
PENGYOS_IT=1 python3 -m pytest -q
```

## License

MIT — Copyright (c) 2026 Pat Wendorf. See `LICENSE`.
