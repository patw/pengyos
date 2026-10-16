"""The tree walker: the part of pengyos-storage that touches the filesystem.

The walker takes an injectable ``scandir``/``stat`` pair, so the test suite can
drive hard links, symlinks, foreign-device directories and permission errors
from a synthetic tree — offline, without root, and without ever touching a real
disk. The production call passes :func:`os.scandir` and :func:`os.stat`.

Rules the numbers must obey (spec.md → "Measurement rules"):

* disk usage (``st_blocks * 512``) by default, apparent size on ``--apparent``;
* a hard link is counted once, at the first place it is seen;
* symlinks are never followed and contribute their own size;
* a directory on another device is a mount point: reported, and with
  ``--one-file-system`` not walked;
* an unreadable directory is a warning, not a failure;
* excluded space is recorded as excluded, never dropped.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import time
from datetime import datetime, timezone
from stat import S_ISDIR

from .model import BLOCK, Filesystem, Node, ScanError, ScanResult

#: Filesystem types that are kernel bookkeeping, not storage. Skipped by
#: ``disks`` so the list answers "which disk is full", not "how many procfs
#: mounts do I have".
PSEUDO_FSTYPES = frozenset({
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "cgroup", "cgroup2",
    "autofs", "binfmt_misc", "securityfs", "pstore", "bpf", "tracefs",
    "debugfs", "configfs", "fusectl", "mqueue", "ramfs", "rpc_pipefs",
    "nsfs", "squashfs", "efivarfs", "fuse.portal", "fuse.gvfsd-fuse",
    "overlay", "sunrpc", "none", "hugetlbfs", "devfs", "fuse.lxcfs",
    "bpftracefs", "configfs", "selinuxfs", "cgroup2",
})

#: Network filesystems. They are storage, but measuring one can block on a
#: dead server, so ``disks`` skips them unless ``--network`` asks for them.
NETWORK_FSTYPES = frozenset({"nfs", "nfs4", "cifs", "smb2", "smb3", "9p", "virtiofs", "sshfs"})


def _size_of(st, mode: str) -> int:
    """The space ``st`` costs: disk usage by default, ``st_size`` on apparent."""
    if mode == "apparent":
        return max(0, int(st.st_size))
    return max(0, int(st.st_blocks)) * BLOCK


def is_excluded(relpath: str, name: str, patterns) -> bool:
    """Match ``--exclude`` patterns against the basename and the relative path.

    A bare pattern (``*.iso``) matches the basename anywhere; a pattern with a
    slash (``cache/*``) matches the path relative to the scan root.
    """
    for pat in patterns:
        if "/" in pat:
            if fnmatch.fnmatch(relpath, pat) or fnmatch.fnmatch(relpath, f"{pat}/*"):
                return True
        elif fnmatch.fnmatch(name, pat):
            return True
    return False


def scan_tree(
    root: str,
    *,
    mode: str = "blocks",
    one_file_system: bool = False,
    excludes=(),
    dedup_hardlinks: bool = True,
    scandir=os.scandir,
    stat=os.stat,
    disk_usage=shutil.disk_usage,
    filesystem: bool = True,
    clock=time.monotonic,
    now=None,
) -> ScanResult:
    """Scan ``root`` and return a :class:`~pengyos_storage.model.ScanResult`.

    Never writes anything, never follows a symlink, and finishes with warnings
    when part of the tree is unreadable.
    """
    if mode not in ("blocks", "apparent"):
        raise ValueError(f"unknown mode: {mode!r}")

    root = os.path.abspath(root)
    started = clock()
    try:
        rst = stat(root)
    except OSError as exc:
        raise ScanError(f"cannot read {root}: {exc.strerror or exc}") from exc
    if not S_ISDIR(rst.st_mode):
        raise ScanError(f"not a directory: {root}")

    root_node = Node(
        path=root,
        relpath=".",
        name=os.path.basename(root.rstrip(os.sep)) or root,
        device=rst.st_dev,
    )
    warnings: list[str] = []
    seen_inodes: set[tuple[int, int]] = set()
    if S_ISDIR(rst.st_mode):  # the root itself is not a file in its parent
        pass

    stack = [root_node]
    # Excluded directories are walked, not linked: their space has to be
    # measured to be reported, and it lands in `excluded_bytes`, never in the
    # tree. That is what makes "excluded" a number instead of a shrug.
    excluded_roots: list[tuple[Node, Node]] = []
    while stack:
        node = stack.pop()
        try:
            entries = sorted(scandir(node.path), key=lambda e: e.name)
        except OSError as exc:
            warnings.append(f"unreadable ({exc.strerror or exc}): {node.path}")
            continue

        for entry in entries:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                warnings.append(f"stat failed ({exc.strerror or exc}): {entry.path}")
                continue

            rel = entry.name if node.relpath == "." else f"{node.relpath}/{entry.name}"
            size = _size_of(st, mode)

            if is_excluded(rel, entry.name, excludes):
                node.excluded_count += 1
                node.excluded_bytes += size
                if S_ISDIR(st.st_mode):
                    sub = Node(path=entry.path, relpath=rel, name=entry.name,
                               device=st.st_dev)
                    excluded_roots.append((node, sub))
                    stack.append(sub)
                continue

            if S_ISDIR(st.st_mode):
                foreign = st.st_dev != node.device
                child = Node(
                    path=entry.path,
                    relpath=rel,
                    name=entry.name,
                    device=st.st_dev,
                    mount=foreign,
                )
                node.children.append(child)
                node.dirs += 1
                if foreign and one_file_system:
                    # Reported, not walked: the space belongs to another disk.
                    child.note = "other filesystem (not scanned)"
                    continue
                stack.append(child)
                continue

            if st.st_nlink > 1 and dedup_hardlinks:
                key = (st.st_dev, st.st_ino)
                if key in seen_inodes:
                    size = 0  # counted where it was first seen
                else:
                    seen_inodes.add(key)

            node.self_bytes += size
            node.files += 1
            node.add_file(_file_entry(entry, size))

    # Fold each excluded subtree's measured space into the directory that
    # excluded it, so `total + excluded` is the whole story.
    for parent, sub in excluded_roots:
        parent.excluded_bytes += sub.finalize() + sum(
            n.excluded_bytes for n in sub.walk()
        )

    root_node.finalize()

    fs = None
    if filesystem:
        fs = _filesystem_for(root, stat=rst, disk_usage=disk_usage)

    if now is None:
        now = datetime.now(timezone.utc)
    return ScanResult(
        root=root,
        mode=mode,
        root_node=root_node,
        warnings=warnings,
        filesystem=fs,
        generated=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        elapsed_s=clock() - started,
    )


def _file_entry(entry, size: int):
    from .model import FileEntry

    return FileEntry(entry.name, size)


def _filesystem_for(path: str, *, stat, disk_usage, mounts_path: str = "/proc/mounts") -> Filesystem | None:
    """The filesystem ``path`` lives on, with its size/used/free.

    Best effort: a box with no readable ``/proc/mounts`` (or a path outside any
    mount) still gets a size/used/free from :func:`shutil.disk_usage`, and a
    total failure to measure yields ``None`` — the report is still useful.
    """
    try:
        total, used, free = disk_usage(path)
    except OSError:
        return None
    device, mountpoint, fstype = "", path, ""
    try:
        best = ""
        for line in open(mounts_path, encoding="utf-8", errors="replace"):
            parts = line.split()
            if len(parts) < 3:
                continue
            dev, mnt, fst = parts[0], parts[1], parts[2]
            mnt = mnt.replace("\\040", " ").replace("\\011", "\t")
            if path == mnt or path.startswith(mnt.rstrip("/") + "/"):
                if len(mnt) > len(best):
                    best, device, mountpoint, fstype = mnt, dev, mnt, fst
    except OSError:
        pass
    return Filesystem(
        device=device, mountpoint=mountpoint, fstype=fstype, size=total, used=used, free=free
    )


def list_filesystems(
    mounts_path: str = "/proc/mounts",
    *,
    disk_usage=shutil.disk_usage,
    pseudo: frozenset[str] = PSEUDO_FSTYPES,
    include_network: bool = False,
) -> list[Filesystem]:
    """Every real mounted filesystem, biggest first (the ``disks`` view).

    Pseudo filesystems are always skipped; network mounts are skipped unless
    ``include_network`` is set, because ``statvfs`` on a dead NFS mount blocks
    for as long as the mount's own timeout.
    """
    out: list[Filesystem] = []
    seen: set[str] = set()
    try:
        lines = open(mounts_path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError as exc:
        raise ScanError(f"cannot read {mounts_path}: {exc.strerror or exc}") from exc

    skip = set(pseudo)
    if not include_network:
        skip |= set(NETWORK_FSTYPES)

    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mountpoint, fstype = parts[0], parts[1], parts[2]
        mountpoint = mountpoint.replace("\\040", " ").replace("\\011", "\t")
        if fstype in skip:
            continue
        if mountpoint in seen:
            continue
        try:
            total, used, free = disk_usage(mountpoint)
        except OSError:
            continue  # a mount we cannot stat (stale NFS, no permission)
        seen.add(mountpoint)
        out.append(Filesystem(
            device=device, mountpoint=mountpoint, fstype=fstype,
            size=total, used=used, free=free,
        ))
    out.sort(key=lambda f: (-f.size, f.mountpoint))
    return out
