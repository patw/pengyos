"""Deterministic ``newc`` cpio archive writer (standard library only).

Implements the ``newc`` (``070701``) cpio format — the same format the Linux
kernel's initramfs unpacker and GNU cpio use — with every source of
non-determinism removed:

* **Inode numbers** are the 1-based position of the entry in the archive
  (never read from the filesystem, where they vary by machine/filesystem).
* **mtime** comes from the caller (the spec's normalized ``[build].mtime``);
  the wall clock is never read.
* **uid/gid** come from the caller (the spec's normalized values).
* **devmajor/devminor/rdevmajor/rdevminor** are always zero (we never pack
  device nodes; readers ignore them).
* Entries are written **strictly in the order they were added**; the
  builder feeds them already-sorted (``Spec.all_paths()``).
* All padding bytes are explicit zeros (no uninitialized memory).

``newc`` stores ownership numerically only (``c_uid``/``c_gid``); there is no
field for user/group *names*, so only numeric ids matter for the output.

Header layout: 6-byte magic + 13 fields, each an 8-digit **uppercase ASCII
hex string** (e.g. ``0000002A``) = **110 bytes**::

    070701 ino mode uid gid nlink mtime filesize
           devmajor devminor rdevmajor rdevminor namesize check

Per the kernel (``init/initramfs.c``) and GNU cpio, each entry is
``header + name-region + data`` where the name-region is the name plus NUL
padded to ``N_ALIGN(len) = (((len) + 1) & ~3) + 2`` bytes and the data is
NUL-padded to a multiple of 4 (this keeps the next header 4-aligned, which
the kernel checks). The archive ends with a ``TRAILER!!!`` entry whose
numeric fields are zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .spec import DEFAULT_MTIME

__all__ = ["MAGIC", "TRAILER_NAME", "HEADER_SIZE", "CpioWriter", "newc_header"]

MAGIC = b"070701"
TRAILER_NAME = b"TRAILER!!!"

# 6 magic bytes + 13 fields x 8 hex digits.
HEADER_SIZE = 110

_S_IFREG = 0o100000
_S_IFLNK = 0o120000
_S_IFDIR = 0o040000
_S_IFMT = 0o170000  # file-type bit mask


def _check32(value: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 0xFFFFFFFF):
        raise ValueError(f"cpio {what}: must be an integer in [0, 2**32), got {value!r}")
    return value


def _hex8(value: int) -> bytes:
    return f"{value:08X}".encode("ascii")


def newc_header(
    *,
    name: bytes,
    mode: int,
    ino: int,
    uid: int,
    gid: int,
    nlink: int,
    mtime: int,
    size: int,
    rdevmajor: int = 0,
    rdevminor: int = 0,
) -> bytes:
    """Return the 110-byte ``newc`` header for one entry.

    ``size`` is the data byte count (file data, or symlink target);
    ``c_namesize`` is ``len(name) + 1`` (NUL terminator). The device-number
    fields (``c_maj``/``c_min``) are always zero: this builder never packs
    device nodes.
    """
    if not name:
        raise ValueError("cpio entry name must be non-empty")
    fields = (
        _check32(ino, "ino"),
        _check32(mode, "mode"),
        _check32(uid, "uid"),
        _check32(gid, "gid"),
        _check32(nlink, "nlink"),
        _check32(mtime, "mtime"),
        _check32(size, "size"),
        0,  # c_maj (device of the file; unused, must be 0)
        0,  # c_min
        _check32(rdevmajor, "rdevmajor"),
        _check32(rdevminor, "rdevminor"),
        len(name) + 1,  # c_namesize includes the NUL terminator
        0,  # c_chksum (zero in newc; only crc/070702 sets it)
    )
    return MAGIC + b"".join(_hex8(v) for v in fields)


def _padded(payload: bytes) -> bytes:
    """NUL-pad *payload* to a multiple of 4 bytes."""
    return payload + b"\x00" * (-len(payload) % 4)


def _name_padded(name: bytes) -> bytes:
    """NUL-pad *name* (already including its NUL) to the kernel's ``N_ALIGN``.

    ``N_ALIGN(len) = (((len) + 1) & ~3) + 2`` — with the 110-byte header this
    makes header+name-region a multiple of 4.
    """
    target = ((len(name) + 1) & ~3) + 2
    return name + b"\x00" * (target - len(name))


@dataclass(frozen=True)
class _Entry:
    name: bytes
    mode: int
    data: bytes


class CpioWriter:
    """Accumulates entries and renders a byte-identical ``newc`` archive.

    All metadata is supplied up front (uid/gid/mtime); nothing is read from
    the filesystem or the wall clock, so ``archive()`` is a pure function of
    (metadata, entries in order).
    """

    def __init__(
        self,
        *,
        uid: int = 0,
        gid: int = 0,
        mtime: datetime = DEFAULT_MTIME,
    ) -> None:
        _check32(uid, "uid")
        _check32(gid, "gid")
        if mtime.tzinfo is None:
            raise ValueError("mtime must be timezone-aware")
        self.uid = uid
        self.gid = gid
        self.mtime = int(mtime.timestamp())
        self._entries: list[_Entry] = []

    # -- entry constructors ------------------------------------------------

    def _add(self, name: str, mode: int, data: bytes) -> None:
        if not name or name.startswith("/") or "\x00" in name:
            raise ValueError(f"invalid cpio entry name: {name!r}")
        self._entries.append(_Entry(name=name.encode("utf-8"), mode=mode, data=data))

    def add_file(self, name: str, data: bytes, *, mode: int = 0o100644) -> None:
        """Add a regular file; *mode* includes the file-type bits (e.g. ``0o100755``)."""
        if (mode & _S_IFMT) != _S_IFREG:
            raise ValueError(f"add_file mode must be a regular file: {mode:#o}")
        self._add(name, mode, data)

    def add_symlink(self, name: str, target: str, *, mode: int = 0o120777) -> None:
        """Add a symlink; the link target is stored as the entry's data."""
        if not target:
            raise ValueError("symlink target must be non-empty")
        if (mode & _S_IFMT) != _S_IFLNK:
            raise ValueError(f"add_symlink mode must be a symlink: {mode:#o}")
        self._add(name, mode, target.encode("utf-8"))

    def add_directory(self, name: str, *, mode: int = 0o040755) -> None:
        """Add a directory entry (name stored with a trailing ``/``, empty data)."""
        if (mode & _S_IFMT) != _S_IFDIR:
            raise ValueError(f"add_directory mode must be a directory: {mode:#o}")
        self._add(name if name.endswith("/") else name + "/", mode, b"")

    # -- rendering ----------------------------------------------------------

    def _entry_bytes(self, ino: int, entry: _Entry) -> bytes:
        header = newc_header(
            name=entry.name,
            mode=entry.mode,
            ino=ino,
            uid=self.uid,
            gid=self.gid,
            nlink=1,
            mtime=self.mtime,
            size=len(entry.data),
        )
        return header + _name_padded(entry.name + b"\x00") + _padded(entry.data)

    def archive(self) -> bytes:
        """Render all added entries (in order) followed by the ``TRAILER!!!`` entry."""
        out = bytearray()
        for ino, entry in enumerate(self._entries, start=1):
            out += self._entry_bytes(ino, entry)
        out += newc_header(
            name=TRAILER_NAME,
            mode=0,
            ino=0,
            uid=0,
            gid=0,
            nlink=0,
            mtime=0,
            size=0,
        )
        out += _name_padded(TRAILER_NAME + b"\x00")
        return bytes(out)
