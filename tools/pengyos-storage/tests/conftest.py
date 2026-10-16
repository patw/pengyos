"""Shared fixtures: a synthetic filesystem the walker can be pointed at.

The suite must exercise hard links, symlinks, directories on a *foreign
device*, and unreadable directories. Doing that on a real disk needs root
(a mount, a hard-linked system file) and is not reproducible, so the walker's
injectable ``scandir``/``stat`` pair is driven by this fake tree instead:
offline, deterministic, no root, and it never touches a real disk.
"""
from __future__ import annotations

import itertools
import os
import stat as stat_mod

import pytest

from pengyos_storage.model import BLOCK


class FakeStat:
    """The attributes the walker reads off ``os.stat_result`` — nothing else."""

    __slots__ = ("st_mode", "st_size", "st_blocks", "st_dev", "st_ino", "st_nlink")

    def __init__(self, *, mode: int, size: int = 0, blocks: int = 0,
                 dev: int = 1, ino: int = 0, nlink: int = 1):
        self.st_mode = mode
        self.st_size = size
        self.st_blocks = blocks
        self.st_dev = dev
        self.st_ino = ino
        self.st_nlink = nlink


class FakeEntry:
    """A ``os.DirEntry`` stand-in: name, path, is_dir, is_symlink, stat.

    ``target`` makes ``stat(follow_symlinks=True)`` resolve the link the way the
    real one does — which is what lets a test prove the walker never follows it.
    """

    def __init__(self, name: str, path: str, st: FakeStat, target: FakeStat | None = None):
        self.name = name
        self.path = path
        self._st = st
        self._target = target

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return stat_mod.S_ISDIR(self.stat(follow_symlinks=follow_symlinks).st_mode)

    def is_symlink(self) -> bool:
        return stat_mod.S_ISLNK(self._st.st_mode)

    def stat(self, *, follow_symlinks: bool = True) -> FakeStat:
        if follow_symlinks and self._target is not None:
            return self._target
        return self._st


def blocks_for(size: int) -> int:
    """Blocks a file of ``size`` bytes occupies on a 512-byte block filesystem."""
    return (size + BLOCK - 1) // BLOCK


class FakeTree:
    """A tree of directories/files, addressable by path, with fake stat."""

    def __init__(self, root: str = "/tree"):
        self.root = os.path.abspath(root)
        self._ino = itertools.count(100)
        self._dirs: dict[str, list[FakeEntry]] = {}
        self._stats: dict[str, FakeStat] = {
            self.root: FakeStat(mode=stat_mod.S_IFDIR | 0o755, dev=1, ino=next(self._ino))
        }
        self._dirs[self.root] = []
        self.unreadable: set[str] = set()

    # -- construction -------------------------------------------------------

    def _parent(self, path: str) -> str:
        parent = os.path.dirname(path.rstrip("/"))
        return parent or self.root

    def mkdir(self, path: str, *, dev: int = 1) -> str:
        path = os.path.abspath(path)
        if path in self._stats:
            return path
        parent = self._parent(path)
        if parent not in self._dirs:
            self.mkdir(parent, dev=dev)
        self._stats[path] = FakeStat(mode=stat_mod.S_IFDIR | 0o755, dev=dev, ino=next(self._ino))
        self._dirs[path] = []
        self._dirs[parent].append(
            FakeEntry(os.path.basename(path), path, self._stats[path])
        )
        return path

    def touch(self, path: str, size: int = 0, *, dev: int = 1, blocks: int | None = None,
              nlink: int = 1, ino: int | None = None) -> str:
        path = os.path.abspath(path)
        self.mkdir(os.path.dirname(path), dev=dev)
        st = FakeStat(
            mode=stat_mod.S_IFREG | 0o644,
            size=size,
            blocks=blocks_for(size) if blocks is None else blocks,
            dev=dev,
            ino=ino if ino is not None else next(self._ino),
            nlink=nlink,
        )
        self._stats[path] = st
        self._dirs[os.path.dirname(path)].append(
            FakeEntry(os.path.basename(path), path, st)
        )
        return path

    def symlink(self, path: str, target: str = "/dev/null", *, size: int = 12, dev: int = 1) -> str:
        path = os.path.abspath(path)
        self.mkdir(os.path.dirname(path), dev=dev)
        st = FakeStat(mode=stat_mod.S_IFLNK | 0o777, size=size, blocks=0, dev=dev,
                      ino=next(self._ino))
        self._stats[path] = st
        # Resolve the target for follow_symlinks=True, like the real DirEntry.
        target_stat = self._stats.get(os.path.abspath(target)) if target else None
        self._dirs[os.path.dirname(path)].append(
            FakeEntry(os.path.basename(path), path, st, target=target_stat)
        )
        return path

    def mark_unreadable(self, path: str) -> None:
        self.unreadable.add(os.path.abspath(path))
    def hardlink(self, link: str, target: str) -> str:
        """A second name for the same inode: same st_ino, same st_dev, nlink+1."""
        link, target = os.path.abspath(link), os.path.abspath(target)
        st = self.stat(target)
        self._stats[link] = st
        st.st_nlink += 1
        self.mkdir(os.path.dirname(link), dev=st.st_dev)
        self._dirs[os.path.dirname(link)].append(
            FakeEntry(os.path.basename(link), link, st)
        )
        return link

    # -- the injectable pair ------------------------------------------------

    def scandir(self, path: str):
        path = os.path.abspath(path)
        if path in self.unreadable:
            raise PermissionError(13, "Permission denied", path)
        if path not in self._dirs:
            raise FileNotFoundError(2, "No such file or directory", path)
        return iter(self._dirs[path])

    def stat(self, path: str) -> FakeStat:
        path = os.path.abspath(path)
        if path not in self._stats:
            raise FileNotFoundError(2, "No such file or directory", path)
        return self._stats[path]

    def scan(self, **kwargs):
        from pengyos_storage import scan_tree

        kwargs.setdefault("filesystem", False)
        kwargs.setdefault("now", _FROZEN_NOW)
        return scan_tree(self.root, scandir=self.scandir, stat=self.stat, **kwargs)


class _FrozenNow:
    """A fixed timestamp so scans are byte-identical across runs."""

    def strftime(self, fmt: str) -> str:
        return "2026-10-15T00:00:00Z"


_FROZEN_NOW = _FrozenNow()


@pytest.fixture
def tree() -> FakeTree:
    """A small, known tree:

        /tree/a/one.txt      1000 B  -> 2 blocks (1024)
        /tree/a/two.txt      2000 B  -> 4 blocks (2048)
        /tree/b/big.bin      5000 B  -> 10 blocks (5120)
        /tree/b/c/deep.log    100 B  -> 1 block  (512)
        /tree/loose.txt        10 B  -> 1 block  (512)
    """
    t = FakeTree("/tree")
    t.mkdir("/tree/a")
    t.mkdir("/tree/b")
    t.mkdir("/tree/b/c")
    t.touch("/tree/a/one.txt", 1000)
    t.touch("/tree/a/two.txt", 2000)
    t.touch("/tree/b/big.bin", 5000)
    t.touch("/tree/b/c/deep.log", 100)
    t.touch("/tree/loose.txt", 10)
    return t


@pytest.fixture
def real_tree(tmp_path):
    """A real on-disk tree, for the CLI tests (no injection, real os.scandir)."""
    (tmp_path / "small").mkdir()
    (tmp_path / "big").mkdir()
    (tmp_path / "small" / "a.txt").write_bytes(b"x" * 100)
    (tmp_path / "big" / "b.bin").write_bytes(b"y" * 5000)
    (tmp_path / "top.txt").write_bytes(b"z" * 700)
    return tmp_path
