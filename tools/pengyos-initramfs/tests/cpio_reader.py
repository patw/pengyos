"""Minimal ``newc`` cpio archive *reader* used only by the test suite.

Deliberately independent from ``pengyos_initramfs.cpio``: it parses raw
bytes (field layout, 4-byte alignment, zero padding, NUL termination, the
trailing ``TRAILER!!!``) so the tests verify the writer's output strictly
instead of round-tripping it through the writer's own assumptions.
"""

from __future__ import annotations

MAGIC = b"070701"
TRAILER_NAME = "TRAILER!!!"
HEADER_SIZE = 110  # 6 magic bytes + 13 fields x 8 hex digits
_HEX_DIGITS = frozenset(b"0123456789ABCDEF")


def n_align(length: int) -> int:
    """Kernel N_ALIGN: round *length* up to the next multiple of 4, plus 2.

    (Matches ``N_ALIGN(len) = (((len) + 1) & ~3) + 2`` in ``init/initramfs.c``;
    with the 110-byte header it yields (name+NUL) padding of ``(-namesize) % 4``.)
    """
    return ((length + 1) & ~3) + 2


class CpioReadError(Exception):
    """Raised when the byte stream is not a well-formed newc archive."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise CpioReadError(msg)


def read_archive(data: bytes) -> list[dict]:
    """Parse a newc archive; returns all entries (including the trailer)."""
    entries: list[dict] = []
    off = 0
    n = len(data)
    while off < n:
        _require(off % 4 == 0, f"unaligned entry at offset {off}")
        _require(off + HEADER_SIZE <= n, f"truncated header at offset {off}")
        header = data[off : off + HEADER_SIZE]
        _require(header[:6] == MAGIC, f"bad magic at offset {off}: {header[:6]!r}")
        raw_fields = header[6:]
        for i in range(0, 104, 8):
            field = raw_fields[i : i + 8]
            _require(
                set(field) <= _HEX_DIGITS,
                f"field at offset {off + 6 + i} is not 8-digit uppercase hex: {field!r}",
            )
        (
            ino, mode, uid, gid, nlink, mtime, size,
            devmajor, devminor, rdevmajor, rdevminor, namesize, check,
        ) = (int(raw_fields[i : i + 8], 16) for i in range(0, 104, 8))
        _require(namesize >= 1, f"namesize < 1 at offset {off}")

        # Name region: header + name + NUL, padded by N_ALIGN (== (-namesize) % 4
        # of extra NULs); then the data region, padded to a multiple of 4.
        name_start = off + HEADER_SIZE
        name_end = name_start + namesize
        _require(name_end <= n, f"name overruns archive at offset {off}")
        name = data[name_start : name_end - 1]
        _require(
            data[name_end - 1] == 0, f"name not NUL-terminated at offset {off}"
        )
        name_region_end = name_start + n_align(namesize)
        data_end = name_region_end + size
        _require(data_end <= n, f"file data overruns archive at offset {off}")
        body = data[name_region_end : data_end]

        next_off = (data_end + 3) // 4 * 4
        _require(
            data[data_end : next_off] == b"\x00" * (next_off - data_end),
            f"padding after entry at offset {off} is not zero-filled",
        )

        entries.append(
            {
                "name": name.decode("utf-8"),
                "ino": ino,
                "mode": mode,
                "uid": uid,
                "gid": gid,
                "nlink": nlink,
                "mtime": mtime,
                "size": size,
                "devmajor": devmajor,
                "devminor": devminor,
                "rdevmajor": rdevmajor,
                "rdevminor": rdevminor,
                "namesize": namesize,
                "check": check,
                "data": body,
            }
        )
        off = next_off

    _require(entries, "archive is empty")
    _require(
        entries[-1]["name"] == TRAILER_NAME,
        f"archive does not end with {TRAILER_NAME!r}",
    )
    return entries


def regular_entries(data: bytes) -> list[dict]:
    """All entries except the trailing ``TRAILER!!!``."""
    return [e for e in read_archive(data) if e["name"] != TRAILER_NAME]
