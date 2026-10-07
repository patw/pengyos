"""Unit tests for the deterministic ``newc`` cpio writer.

Uses the independent reader in ``tests/cpio_reader.py`` to verify the raw
bytes: header layout, field values, alignment, zero padding, inodes, and
trailer — not just a round-trip through our own assumptions.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pengyos_initramfs.cpio import (
    HEADER_SIZE,
    MAGIC,
    CpioWriter,
    newc_header,
)
from cpio_reader import CpioReadError, TRAILER_NAME, read_archive, regular_entries

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIXED = datetime(2001, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
FIXED_EPOCH = int(FIXED.timestamp())


# ---------------------------------------------------------------------------
# newc_header
# ---------------------------------------------------------------------------


def test_header_size_and_field_layout():
    header = newc_header(
        name=b"init",
        mode=0o100755,
        ino=7,
        uid=1,
        gid=2,
        nlink=3,
        mtime=42,
        size=5,
    )
    assert len(header) == HEADER_SIZE == 110
    assert header[:6] == MAGIC
    fields = [int(header[6 + 8 * i : 6 + 8 * i + 8], 16) for i in range(13)]
    ino, mode, uid, gid, nlink, mtime, size, devmaj, devmin, rdevmaj, rdevmin, namesize, check = fields
    assert (ino, mode, uid, gid, nlink, mtime, size) == (7, 0o100755, 1, 2, 3, 42, 5)
    assert (devmaj, devmin) == (0, 0)  # device numbers never set
    assert (rdevmaj, rdevmin, check) == (0, 0, 0)
    assert namesize == len(b"init") + 1


def test_header_fields_are_uptercase_hex_ascii():
    header = newc_header(name=b"x", mode=0o644, ino=1, uid=0, gid=0, nlink=1, mtime=0, size=0)
    for i in range(0, 104, 8):
        field = header[6 + i : 6 + i + 8]
        assert set(field) <= set(b"0123456789ABCDEF"), f"field {i}: {field!r}"


def test_header_rejects_bad_values():
    kwargs = dict(name=b"x", mode=0o644, ino=1, uid=0, gid=0, nlink=1, mtime=0, size=0)
    with pytest.raises(ValueError):
        newc_header(**{**kwargs, "name": b""})
    with pytest.raises(ValueError):
        newc_header(**{**kwargs, "uid": -1})
    with pytest.raises(ValueError):
        newc_header(**{**kwargs, "size": 2**32})
    with pytest.raises(ValueError):
        newc_header(**{**kwargs, "ino": True})  # bool is not a valid field value


# ---------------------------------------------------------------------------
# CpioWriter
# ---------------------------------------------------------------------------


def test_writer_roundtrip_order_inodes_and_trailer():
    w = CpioWriter(uid=10, gid=20, mtime=FIXED)
    w.add_file("a.txt", b"hi", mode=0o100644)
    w.add_symlink("lnk", "a.txt")
    w.add_directory("dir")
    entries = read_archive(w.archive())

    assert [e["name"] for e in entries] == ["a.txt", "lnk", "dir/", TRAILER_NAME]
    assert [e["ino"] for e in entries[:-1]] == [1, 2, 3]

    a, lnk, d, trailer = entries
    assert a["data"] == b"hi" and a["mode"] == 0o100644
    assert lnk["data"] == b"a.txt" and lnk["mode"] == 0o120777
    assert d["data"] == b"" and d["mode"] == 0o040755
    assert all(e["uid"] == 10 and e["gid"] == 20 for e in entries[:-1])
    assert all(e["mtime"] == FIXED_EPOCH for e in entries[:-1])
    assert all(e["nlink"] == 1 for e in entries[:-1])

    # Trailer entry: all-zero metadata.
    assert trailer["ino"] == trailer["mode"] == trailer["uid"] == 0
    assert trailer["gid"] == trailer["nlink"] == trailer["mtime"] == 0
    assert trailer["size"] == 0 and trailer["data"] == b""


def test_writer_is_deterministic_and_order_sensitive():
    def build(order):
        w = CpioWriter(uid=0, gid=0, mtime=EPOCH)
        for name, data in order:
            w.add_file(name, data)
        return w.archive()

    order = [("z/one", b"1"), ("a/two", b"22"), ("m/three", b"333")]
    assert build(order) == build(order)  # same inputs -> identical bytes
    assert build(order) != build(list(reversed(order)))  # order matters


def test_writer_normalizes_metadata_never_wall_clock():
    w = CpioWriter(mtime=FIXED)
    w.add_file("x", b"y")
    entries = regular_entries(w.archive())
    assert entries and all(e["mtime"] == FIXED_EPOCH for e in entries)
    assert all(e["uid"] == 0 and e["gid"] == 0 for e in entries)


def test_writer_rejects_naive_mtime_and_bad_ids():
    with pytest.raises(ValueError):
        CpioWriter(mtime=datetime(2000, 1, 1))  # naive
    with pytest.raises(ValueError):
        CpioWriter(uid=2**32)
    with pytest.raises(ValueError):
        CpioWriter(gid=-5)


def test_writer_enforces_type_bits_and_names():
    w = CpioWriter()
    with pytest.raises(ValueError):
        w.add_file("f", b"x", mode=0o644)  # no S_IFREG
    with pytest.raises(ValueError):
        w.add_symlink("l", "t", mode=0o100644)  # no S_IFLNK
    with pytest.raises(ValueError):
        w.add_directory("d", mode=0o100644)  # no S_IFDIR
    with pytest.raises(ValueError):
        w.add_symlink("l", "")  # empty target
    with pytest.raises(ValueError):
        w.add_file("/abs", b"x")
    with pytest.raises(ValueError):
        w.add_file("bad\x00name", b"x")


def test_name_region_uses_kernel_n_align_padding():
    """Name-region padding must equal the kernel's N_ALIGN, name-length by name-length."""
    w = CpioWriter(mtime=EPOCH)
    # Names of lengths 1..6 exercise N_ALIGN paddings of 1, 0, 3, 2, 1, 0.
    for i, name in enumerate(["a", "bb", "ccc", "dddd", "eeeee", "ffffff"]):
        w.add_file(name, b"x" * i)
    data = w.archive()
    assert len(data) % 4 == 0
    # The reader re-derives N_ALIGN from the header and would reject any
    # deviation (it splits name-region vs data off these exact boundaries).
    read_archive(data)


def test_archive_is_4_byte_aligned_and_padding_zero():
    w = CpioWriter(mtime=EPOCH)
    w.add_file("a", b"x")
    w.add_file("bb", b"xy")
    w.add_file("ccc", b"xyz")
    w.add_file("dddd", b"abcd")
    data = w.archive()
    assert len(data) % 4 == 0
    read_archive(data)  # reader asserts zero-filled padding + alignment


def test_empty_archive_is_trailer_only():
    entries = read_archive(CpioWriter(mtime=EPOCH).archive())
    assert [e["name"] for e in entries] == [TRAILER_NAME]


def test_reader_rejects_garbage():
    for bad in (b"", b"070701" * 20, b"\x1f\x8b\x08\x00"):
        with pytest.raises(CpioReadError):
            read_archive(bad)
