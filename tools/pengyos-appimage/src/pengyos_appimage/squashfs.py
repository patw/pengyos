"""Read files out of an AppImage's squashfs payload — pure Python, read-only.

The payload is a squashfs 4.0 image starting at the offset
:mod:`pengyos_appimage.runtime` finds. This module reads it directly, the way
the kernel's squashfs driver does, so the tool needs no ``unsquashfs``, no FUSE
mount, and never runs the AppImage. Nothing is written to disk: callers ask for
a path (or walk the tree) and get bytes back.

Supported compressors are the ones Python's standard library can decompress:
gzip (zlib), lzma, xz, and zstd (``compression.zstd``, Python 3.14+). AppImages
are built with zstd or xz by current tools and gzip by older ones; lzo and lz4
payloads are reported, not read.

The payload is untrusted input, so every read is bounded: decompression stops at
the block size the superblock declares, metadata reads stop at the table they
belong to, directory walks refuse loops, and symlinks are followed only inside
the payload, a bounded number of times.

Layout reference: the Linux kernel's ``fs/squashfs/squashfs_fs.h``.
"""

from __future__ import annotations

import posixpath
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

SQUASHFS_MAGIC = 0x73717368  # b"hsqs" little-endian

_SUPERBLOCK_FMT = "<IIIIIHHHHHHQQQQQQQQ"
_SUPERBLOCK_SIZE = struct.calcsize(_SUPERBLOCK_FMT)  # 96

_METADATA_SIZE = 8192
_METADATA_UNCOMPRESSED = 0x8000
_DATA_UNCOMPRESSED = 1 << 24
_NO_FRAGMENT = 0xFFFFFFFF
_INVALID_FRAGMENT_TABLE = 0xFFFFFFFFFFFFFFFF

_COMPRESSORS = {1: "gzip", 2: "lzma", 3: "lzo", 4: "xz", 5: "lz4", 6: "zstd"}

# Inode types.
DIR, FILE, SYMLINK = 1, 2, 3
LDIR, LREG, LSYMLINK = 8, 9, 10
_DIR_TYPES = (DIR, LDIR)
_FILE_TYPES = (FILE, LREG)
_SYMLINK_TYPES = (SYMLINK, LSYMLINK)

# Bounds against hostile images.
_MAX_SYMLINK_HOPS = 40
_MAX_DEPTH = 64
_MAX_FILE_READ = 1 << 30  # 1 GiB: no app file the tool reads is near this


class SquashfsError(RuntimeError):
    """Raised when the payload cannot be read."""


@dataclass(frozen=True)
class Inode:
    """The parts of a squashfs inode the reader uses."""

    type: int
    #: Directory: (dir_block_start, block_offset, listing_size).
    #: File: (blocks_start, fragment_index, fragment_offset, file_size, sizes).
    #: Symlink: the target string.
    data: object

    @property
    def is_dir(self) -> bool:
        return self.type in _DIR_TYPES

    @property
    def is_file(self) -> bool:
        return self.type in _FILE_TYPES

    @property
    def is_symlink(self) -> bool:
        return self.type in _SYMLINK_TYPES

    @property
    def size(self) -> int:
        return self.data[3] if self.is_file else 0


class SquashFS:
    """A read-only view of a squashfs image at *offset* inside *fh*."""

    def __init__(self, fh: BinaryIO, offset: int = 0):
        self._fh = fh
        self._base = offset
        raw = self._read(0, _SUPERBLOCK_SIZE)
        if len(raw) < _SUPERBLOCK_SIZE:
            raise SquashfsError("squashfs superblock truncated")
        (
            magic, _inodes, _mtime, self.block_size, self._frag_count,
            compressor, block_log, _flags, _ids, major, minor,
            self._root_ref, self._bytes_used, _id_table, _xattr_table,
            self._inode_table, self._dir_table, self._frag_table, _export,
        ) = struct.unpack(_SUPERBLOCK_FMT, raw)
        if magic != SQUASHFS_MAGIC:
            raise SquashfsError("no squashfs magic at the payload offset")
        if (major, minor) != (4, 0):
            raise SquashfsError(f"squashfs {major}.{minor} is not supported (4.0 only)")
        if self.block_size != 1 << block_log or not 4096 <= self.block_size <= 1 << 20:
            raise SquashfsError(f"implausible squashfs block size {self.block_size}")
        if not (
            _SUPERBLOCK_SIZE <= self._inode_table < self._dir_table <= self._bytes_used
        ):
            raise SquashfsError("squashfs table offsets are inconsistent")
        self.compression = _COMPRESSORS.get(compressor, f"id-{compressor}")
        self._decompress = _decompressor(self.compression)
        self._metadata_cache: dict[int, tuple[bytes, int]] = {}
        self._fragments: dict[int, tuple[int, int]] | None = None

    @classmethod
    def open(cls, path: str | Path, offset: int) -> "SquashFS":
        """Open the payload of the AppImage at *path* (caller closes via ``close``)."""
        return cls(Path(path).open("rb"), offset)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "SquashFS":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- public API ----------------------------------------------------------

    def root(self) -> Inode:
        return self._inode(self._root_ref)

    def listdir(self, inode: Inode) -> list[tuple[str, Inode]]:
        """Return ``(name, inode)`` for every entry of directory *inode*."""
        if not inode.is_dir:
            raise SquashfsError("not a directory")
        block, offset, size = inode.data
        # The stored size counts "." and ".." (3 bytes) that are not stored.
        listing = self._metadata(self._dir_table, block, offset, max(size - 3, 0))
        entries: list[tuple[str, Inode]] = []
        pos = 0
        while pos + 12 <= len(listing):
            count, start, _inode_base = struct.unpack_from("<III", listing, pos)
            pos += 12
            for _ in range(count + 1):
                if pos + 8 > len(listing):
                    raise SquashfsError("directory listing truncated")
                ioff, _delta, _type, name_size = struct.unpack_from(
                    "<HhHH", listing, pos
                )
                pos += 8
                name = listing[pos:pos + name_size + 1].decode("utf-8", "surrogateescape")
                pos += name_size + 1
                if not name or "/" in name or name in (".", ".."):
                    raise SquashfsError(f"invalid directory entry name {name!r}")
                entries.append((name, self._inode((start << 16) | ioff)))
        return entries

    def walk(self) -> Iterator[tuple[str, Inode]]:
        """Yield ``(path, inode)`` for every entry, depth first, root excluded."""
        seen: set[tuple] = set()

        def visit(inode: Inode, prefix: str, depth: int):
            if depth > _MAX_DEPTH:
                raise SquashfsError("directory tree too deep")
            key = inode.data
            if key in seen:
                raise SquashfsError("directory loop in payload")
            seen.add(key)
            for name, child in sorted(self.listdir(inode), key=lambda e: e[0]):
                path = f"{prefix}{name}"
                yield path, child
                if child.is_dir:
                    yield from visit(child, path + "/", depth + 1)

        yield from visit(self.root(), "", 0)

    def lookup(self, path: str, follow: bool = True) -> Inode | None:
        """Return the inode at *path* (relative to the payload root), or None.

        Relative symlinks are followed inside the payload. An absolute symlink
        points at the host at run time, not into the payload, so it resolves to
        None here.
        """
        hops = 0
        parts = [p for p in path.split("/") if p not in ("", ".")]
        stack: list[Inode] = [self.root()]
        while parts:
            part = parts.pop(0)
            if part == "..":
                if len(stack) > 1:
                    stack.pop()
                continue
            match = next((i for n, i in self.listdir(stack[-1]) if n == part), None)
            if match is None:
                return None
            if match.is_symlink and (follow or parts):
                hops += 1
                if hops > _MAX_SYMLINK_HOPS:
                    raise SquashfsError("too many symlink hops")
                target = match.data
                if target.startswith("/"):
                    return None
                parts = [p for p in target.split("/") if p not in ("", ".")] + parts
                continue
            if parts and not match.is_dir:
                return None
            stack.append(match)
        return stack[-1]

    def read(self, inode: Inode, limit: int | None = None) -> bytes:
        """Return the contents of file *inode* (the first *limit* bytes, if set)."""
        if not inode.is_file:
            raise SquashfsError("not a regular file")
        start, frag_index, frag_offset, size, sizes = inode.data
        want = size if limit is None else min(size, limit)
        if want > _MAX_FILE_READ:
            raise SquashfsError(f"file too large to read ({size} bytes)")
        out = bytearray()
        pos = start
        for i, word in enumerate(sizes):
            if len(out) >= want:
                break
            on_disk = word & ~_DATA_UNCOMPRESSED
            expected = min(self.block_size, size - i * self.block_size)
            if on_disk == 0:  # sparse block
                out += bytes(expected)
                continue
            raw = self._read(pos, on_disk)
            pos += on_disk
            block = raw if word & _DATA_UNCOMPRESSED else self._decompress(raw, self.block_size)
            out += block[:expected]
        if len(out) < want and frag_index != _NO_FRAGMENT:
            frag = self._fragment_block(frag_index)
            tail = size - len(sizes) * self.block_size
            out += frag[frag_offset:frag_offset + tail]
        if len(out) < want:
            raise SquashfsError("file data truncated")
        return bytes(out[:want])

    def read_path(self, path: str, limit: int | None = None) -> bytes | None:
        """Read the file at *path*, following symlinks; None if absent."""
        inode = self.lookup(path)
        if inode is None or not inode.is_file:
            return None
        return self.read(inode, limit)

    # --- internals -----------------------------------------------------------

    def _read(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0:
            raise SquashfsError("negative offset in payload")
        self._fh.seek(self._base + offset)
        return self._fh.read(size)

    def _metadata_block(self, pos: int) -> tuple[bytes, int]:
        """Decompress the metadata block at *pos*; return (data, next_pos)."""
        cached = self._metadata_cache.get(pos)
        if cached is not None:
            return cached
        header = self._read(pos, 2)
        if len(header) < 2:
            raise SquashfsError("metadata block header truncated")
        word, = struct.unpack("<H", header)
        length = word & ~_METADATA_UNCOMPRESSED & 0xFFFF
        if not 0 < length <= _METADATA_SIZE:
            raise SquashfsError(f"bad metadata block length {length}")
        raw = self._read(pos + 2, length)
        data = raw if word & _METADATA_UNCOMPRESSED else self._decompress(raw, _METADATA_SIZE)
        result = (data, pos + 2 + length)
        self._metadata_cache[pos] = result
        return result

    def _metadata(self, table: int, block: int, offset: int, size: int) -> bytes:
        """Read *size* bytes of metadata starting at (block, offset) of *table*."""
        out = bytearray()
        pos = table + block
        while len(out) < size:
            if pos >= self._bytes_used:
                raise SquashfsError("metadata read runs past the image")
            data, nxt = self._metadata_block(pos)
            out += data[offset:]
            offset = 0
            pos = nxt
        return bytes(out[:size])

    def _metadata_reader(self, table: int, ref: int):
        """Return a ``take(n)`` function reading sequentially from *ref*."""
        state = {"pos": table + (ref >> 16), "buf": b"", "off": ref & 0xFFFF}
        data, nxt = self._metadata_block(state["pos"])
        state["buf"], state["pos"] = data[state["off"]:], nxt

        def take(n: int) -> bytes:
            while len(state["buf"]) < n:
                if state["pos"] >= self._bytes_used:
                    raise SquashfsError("inode runs past the image")
                data, nxt = self._metadata_block(state["pos"])
                state["buf"] += data
                state["pos"] = nxt
            chunk, state["buf"] = state["buf"][:n], state["buf"][n:]
            return chunk

        return take

    def _inode(self, ref: int) -> Inode:
        take = self._metadata_reader(self._inode_table, ref)
        itype, = struct.unpack("<H", take(16)[:2])
        if itype == DIR:
            block, _links, size, offset, _parent = struct.unpack("<IIHHI", take(16))
            return Inode(itype, (block, offset, size))
        if itype == LDIR:
            _links, size, block, _parent, _idx, offset, _xattr = struct.unpack(
                "<IIIIHHI", take(24)
            )
            return Inode(itype, (block, offset, size))
        if itype == FILE:
            start, frag, foff, size = struct.unpack("<IIII", take(16))
        elif itype == LREG:
            start, size, _sparse, _links, frag, foff, _xattr = struct.unpack(
                "<QQQIIII", take(40)
            )
        elif itype in _SYMLINK_TYPES:
            _links, tsize = struct.unpack("<II", take(8))
            if tsize > 4096:
                raise SquashfsError("symlink target too long")
            return Inode(itype, take(tsize).decode("utf-8", "surrogateescape"))
        else:
            # Device nodes, fifos, sockets: nothing to read.
            return Inode(itype, None)
        if size > 1 << 40:
            raise SquashfsError("implausible file size")
        if frag == _NO_FRAGMENT:
            nblocks = -(-size // self.block_size)
        else:
            nblocks = size // self.block_size
        sizes = struct.unpack(f"<{nblocks}I", take(4 * nblocks))
        return Inode(itype, (start, frag, foff, size, sizes))

    def _fragment_block(self, index: int) -> bytes:
        if self._fragments is None:
            self._fragments = {}
        if index in self._fragments:
            start, word = self._fragments[index]
        else:
            if self._frag_table == _INVALID_FRAGMENT_TABLE or index >= self._frag_count:
                raise SquashfsError(f"fragment {index} out of range")
            per_block = _METADATA_SIZE // 16
            lookup = self._read(self._frag_table + 8 * (index // per_block), 8)
            if len(lookup) < 8:
                raise SquashfsError("fragment table truncated")
            block_pos, = struct.unpack("<Q", lookup)
            data, _ = self._metadata_block(block_pos)
            entry = data[(index % per_block) * 16:(index % per_block) * 16 + 16]
            if len(entry) < 16:
                raise SquashfsError("fragment entry truncated")
            start, word, _unused = struct.unpack("<QII", entry)
            self._fragments[index] = (start, word)
        on_disk = word & ~_DATA_UNCOMPRESSED
        raw = self._read(start, on_disk)
        return raw if word & _DATA_UNCOMPRESSED else self._decompress(raw, self.block_size)


def _decompressor(name: str):
    """Return ``f(raw, max_out) -> bytes`` for compressor *name*.

    Every decompressor is capped at *max_out*, so a hostile block cannot expand
    without bound.
    """
    if name == "gzip":
        def gzip(raw: bytes, max_out: int) -> bytes:
            try:
                return zlib.decompressobj().decompress(raw, max_out)
            except zlib.error as exc:
                raise SquashfsError(f"gzip block is corrupt: {exc}") from exc
        return gzip
    if name in ("xz", "lzma"):
        import lzma

        fmt = lzma.FORMAT_XZ if name == "xz" else lzma.FORMAT_ALONE

        def xz(raw: bytes, max_out: int) -> bytes:
            try:
                return lzma.LZMADecompressor(format=fmt).decompress(raw, max_out)
            except lzma.LZMAError as exc:
                raise SquashfsError(f"{name} block is corrupt: {exc}") from exc
        return xz
    if name == "zstd":
        try:
            from compression import zstd as _zstd
        except ImportError as exc:  # Python < 3.14, or built without libzstd
            raise SquashfsError(
                "zstd payload needs Python 3.14+ with compression.zstd"
            ) from exc

        def zstd(raw: bytes, max_out: int) -> bytes:
            try:
                return _zstd.ZstdDecompressor().decompress(raw, max_out)
            except _zstd.ZstdError as exc:
                raise SquashfsError(f"zstd block is corrupt: {exc}") from exc
        return zstd
    raise SquashfsError(f"{name} compression is not supported (gzip, xz, lzma, zstd only)")
