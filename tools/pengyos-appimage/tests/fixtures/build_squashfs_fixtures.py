#!/usr/bin/env python3
"""Build the committed squashfs fixtures for ``tests/test_squashfs.py``.

One tree, packed by the real ``mksquashfs`` with each compressor and layout the
reader must handle. The tests import :func:`expected` from this file, so the
tree and the assertions cannot drift apart.

The tree covers: an incompressible file spanning several 4 KiB blocks (stored
uncompressed) and a compressible one (stored compressed), tail-end fragments, an
empty file, a sparse file (all-zero blocks are stored as size 0), a directory
of 300 entries (squashfs splits listings into several headers), nested
directories, and relative, absolute, chained, looping and directory symlinks.

Usage::

    python3 tests/fixtures/build_squashfs_fixtures.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent

# name -> extra mksquashfs arguments
VARIANTS = {
    # -tailends: pack the tail of a multi-block file into a fragment (off by
    # default since squashfs-tools 4.4), so that read path is exercised.
    "sq-gzip.sqfs": ["-comp", "gzip", "-tailends"],
    "sq-xz.sqfs": ["-comp", "xz", "-tailends"],
    "sq-zstd.sqfs": ["-comp", "zstd", "-tailends"],
    "sq-lzma.sqfs": ["-comp", "lzma", "-tailends"],
    "sq-nofrag.sqfs": ["-comp", "gzip", "-no-fragments"],
    "sq-raw.sqfs": ["-comp", "gzip", "-noI", "-noD", "-noF", "-noX"],
    "sq-lz4.sqfs": ["-comp", "lz4"],  # read must refuse it by name
}


def _pattern(n: int, seed: int) -> bytes:
    """Deterministic, poorly compressible bytes."""
    out = bytearray()
    x = seed
    while len(out) < n:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out += x.to_bytes(4, "little")
    return bytes(out[:n])


def expected() -> tuple[dict[str, bytes], dict[str, str], set[str]]:
    """Return (files, symlinks, dirs) of the tree, paths relative to its root."""
    files = {
        "big.bin": _pattern(4096 * 3 + 1000, 1),  # 3 full blocks + a tail
        "text.bin": b"pengyos squashfs reader " * 700,  # compressible: 4 blocks + a tail
        "small.txt": b"hello from a fragment\n",
        "empty": b"",
        "sparse.bin": bytes(4096 * 3) + b"tail",
        "a/b/c/deep.txt": b"deep\n",
    }
    for i in range(300):
        files[f"many/f{i:03d}"] = f"{i}\n".encode()
    links = {
        "link-rel": "small.txt",
        "link-chain": "link-rel",
        "link-abs": "/etc/hostname",
        "loop-a": "loop-b",
        "loop-b": "loop-a",
        "dirlink": "a/b",
        "a/b/up": "../../small.txt",
    }
    dirs = {"a", "a/b", "a/b/c", "many"}
    return files, links, dirs


def populate(root: Path) -> None:
    files, links, dirs = expected()
    for d in sorted(dirs):
        (root / d).mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        (root / rel).write_bytes(data)
    for rel, target in links.items():
        os.symlink(target, root / rel)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        populate(tree)
        for name, extra in VARIANTS.items():
            out = FIXTURES / name
            subprocess.run(
                ["mksquashfs", str(tree), str(out), "-noappend", "-root-owned",
                 "-b", "4096", "-mkfs-time", "0", "-all-time", "0", "-quiet",
                 "-no-progress", *extra],
                check=True,
            )
            print(f"wrote {out.name} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
