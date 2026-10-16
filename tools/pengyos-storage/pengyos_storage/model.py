"""The data model and the size arithmetic for pengyos-storage.

Everything here is pure: no filesystem access, no globals that a test has to
reset. The walker (``scan.py``) builds these, the renderers
(``report.py`` / ``html.py``) read them.
"""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from typing import Any

#: POSIX fixes ``st_blocks`` at 512-byte units regardless of the filesystem's
#: block size, so disk usage is always ``st_blocks * 512``.
BLOCK = 512

#: How many individual files a single directory keeps in the report. The scan
#: trims to this so a directory with a million files does not cost a million
#: objects; the totals stay exact because the trim happens after the sum.
FILE_CAP = 100

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB")


class ScanError(Exception):
    """A scan that cannot start: the path is missing, unreadable, not a directory."""


@dataclass
class FileEntry:
    """One file, as reported inside a directory's own listing."""

    name: str
    bytes: int


@dataclass
class Node:
    """A directory in the scanned tree.

    ``self_bytes`` is the space of the files *directly* in this directory;
    ``bytes`` is that plus every descendant, computed by :meth:`finalize`.
    ``excluded_bytes`` is space the user excluded with ``--exclude``: it is
    kept visible so the arithmetic closes, never silently dropped.
    """

    path: str
    relpath: str
    name: str
    device: int = 0
    mount: bool = False
    note: str = ""
    self_bytes: int = 0
    files: int = 0
    dirs: int = 0
    excluded_bytes: int = 0
    excluded_count: int = 0
    children: list["Node"] = field(default_factory=list)
    files_here: list[FileEntry] = field(default_factory=list)
    bytes: int = 0
    total_files: int = 0
    total_dirs: int = 0
    max_file_bytes: int = 0

    def sort_key(self) -> tuple[int, str]:
        """Deterministic ordering: biggest first, ties broken by name."""
        return (-self.bytes, self.name)

    def finalize(self) -> int:
        """Compute subtree totals bottom-up; return this node's total bytes."""
        total = self.self_bytes
        files = self.files
        dirs = self.dirs
        for child in self.children:
            total += child.finalize()
            files += child.total_files
            dirs += child.total_dirs
        self.bytes = total
        self.total_files = files
        self.total_dirs = dirs
        self.children.sort(key=lambda c: c.sort_key())
        here = sorted(self.files_here, key=lambda f: (-f.bytes, f.name))
        self.files_here = here[:FILE_CAP]
        self.max_file_bytes = here[0].bytes if here else 0
        return total

    def add_file(self, entry: FileEntry) -> None:
        """Record a file, trimming the listing so it stays bounded."""
        self.files_here.append(entry)
        if len(self.files_here) > FILE_CAP * 2:
            self.files_here = heapq.nlargest(
                FILE_CAP, self.files_here, key=lambda f: (f.bytes, f.name)
            )

    def walk(self, depth: int = 0) -> list["Node"]:
        """This node and every descendant, in report order (biggest first)."""
        out = [self]
        if self.children:
            for child in self.children:
                out.extend(child.walk(depth + 1))
        return out

    def to_dict(self) -> dict[str, Any]:
        """The JSON form of this node (schema 1)."""
        return {
            "path": self.path,
            "relpath": self.relpath,
            "name": self.name,
            "bytes": self.bytes,
            "self_bytes": self.self_bytes,
            "files": self.files,
            "dirs": self.dirs,
            "total_files": self.total_files,
            "total_dirs": self.total_dirs,
            "excluded_bytes": self.excluded_bytes,
            "excluded_count": self.excluded_count,
            "mount": self.mount,
            "note": self.note,
            "files_here": [{"name": f.name, "bytes": f.bytes} for f in self.files_here],
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class Filesystem:
    """One mounted filesystem, for the ``disks`` view."""

    device: str
    mountpoint: str
    fstype: str = ""
    size: int = 0
    used: int = 0
    free: int = 0

    @property
    def used_pct(self) -> float:
        return (self.used / self.size * 100.0) if self.size else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "mountpoint": self.mountpoint,
            "fstype": self.fstype,
            "size": self.size,
            "used": self.used,
            "free": self.free,
            "used_pct": round(self.used_pct, 2),
        }


@dataclass
class ScanResult:
    """The outcome of one tree scan."""

    root: str
    mode: str  # "blocks" | "apparent"
    root_node: Node
    warnings: list[str] = field(default_factory=list)
    filesystem: Filesystem | None = None
    generated: str = ""
    elapsed_s: float = 0.0

    @property
    def total_bytes(self) -> int:
        return self.root_node.bytes

    @property
    def excluded_bytes(self) -> int:
        return sum(n.excluded_bytes for n in self.root_node.walk())

    def to_dict(self) -> dict[str, Any]:
        from . import SCHEMA, TOOL, VERSION

        # `elapsed_s` is deliberately absent: the spec promises two scans of an
        # unchanged tree are byte-identical apart from `generated`, and a timing
        # field would break that for every consumer that diffs documents.
        return {
            "tool": TOOL,
            "version": VERSION,
            "schema": SCHEMA,
            "root": self.root,
            "mode": self.mode,
            "generated": self.generated,
            "total_bytes": self.total_bytes,
            "file_count": self.root_node.total_files,
            "dir_count": self.root_node.total_dirs,
            "excluded_bytes": self.excluded_bytes,
            "warnings": list(self.warnings),
            "filesystem": self.filesystem.to_dict() if self.filesystem else None,
            "tree": self.root_node.to_dict(),
        }


# ---------------------------------------------------------------------------
# Size formatting / parsing
# ---------------------------------------------------------------------------


def human(n: int, space: bool = True) -> str:
    """A byte count in binary units: ``1536`` -> ``1.5 KiB``.

    Always the same width for the same magnitude so a column of sizes lines up.
    """
    sep = " " if space else ""
    value = float(n)
    for unit in _UNITS:
        if abs(value) < 1024.0 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(round(value))}{sep}{unit}"
            return f"{value:.1f}{sep}{unit}"
        value /= 1024.0
    return f"{value:.1f}{sep}{_UNITS[-1]}"  # pragma: no cover - unreachable


_SUFFIX = {
    "": 1,
    "B": 1,
    "K": 1024, "KI": 1024, "KIB": 1024,
    "M": 1024 ** 2, "MI": 1024 ** 2, "MIB": 1024 ** 2,
    "G": 1024 ** 3, "GI": 1024 ** 3, "GIB": 1024 ** 3,
    "T": 1024 ** 4, "TI": 1024 ** 4, "TIB": 1024 ** 4,
    "P": 1024 ** 5, "PI": 1024 ** 5, "PIB": 1024 ** 5,
}

_SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]*)\s*$")


def parse_size(text: str) -> int:
    """Parse a size like ``10M``, ``1.5G``, ``4096`` into bytes.

    Suffixes are binary and case-insensitive (``K``/``Ki``/``KiB``). Anything
    else raises ``ValueError`` so a typo in ``--min-size`` is an error, not a
    silent zero.
    """
    m = _SIZE_RE.match(text or "")
    if not m:
        raise ValueError(f"not a size: {text!r}")
    number, suffix = m.group(1), m.group(2).upper()
    if suffix not in _SUFFIX:
        raise ValueError(f"unknown size suffix {m.group(2)!r} in {text!r}")
    return int(round(float(number) * _SUFFIX[suffix]))


def bar(fraction: float, width: int = 24) -> str:
    """A text bar of ``width`` cells filled in proportion to ``fraction``."""
    try:
        f = float(fraction)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        f = 0.0
    if f != f:  # NaN
        f = 0.0
    f = max(0.0, min(1.0, f))
    filled = int(round(f * width))
    return "█" * filled + "░" * (width - filled)


def percent(part: int, total: int) -> float:
    """``part`` as a percentage of ``total``; 0 when the total is 0."""
    return (part / total * 100.0) if total > 0 else 0.0
