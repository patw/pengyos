"""Tests for :mod:`pengyos_appimage.trailer`."""

from __future__ import annotations

import struct

import pytest

from pengyos_appimage import trailer


def _make_trailer(
    metadata: bytes, section_name: str = ".appimage"
) -> bytes:
    """Build a minimal ELF trailer with one data section.

    Mirrors tests/fixtures/build_sample_appimage.py so these tests do not depend
    on the committed binary fixture or on ``mksquashfs``.
    """
    elf64_ehdr = 64
    elf64_shdr = 64

    def shdr(*fields):
        return struct.pack("<IIQQQQIIQQ", *fields)

    shstr = (
        b"\x00"
        + section_name.encode()
        + b"\x00.shstrtab\x00"
    )
    name_section = 1
    name_shstrtab = 1 + len(section_name.encode()) + 1
    mlen = len(metadata)
    slen = len(shstr)
    section_off = elf64_ehdr
    shstrtab_off = elf64_ehdr + mlen
    shoff = elf64_ehdr + mlen + slen

    shdrs = (
        shdr(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        + shdr(name_section, 1, 2, 0, section_off, mlen, 0, 0, 1, 0)
        + shdr(name_shstrtab, 3, 0, 0, shstrtab_off, slen, 0, 0, 1, 0)
    )
    e_ident = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
    ehdr = e_ident + struct.pack(
        "<HHIQQQIHHHHHH", 2, 0x3E, 1, 0, 0, shoff, 0, elf64_ehdr, 0, 0,
        elf64_shdr, 3, 2,
    )
    body = ehdr + metadata + shstr + shdrs
    trailer_size = len(body) + 16
    return body + struct.pack("<II", trailer_size, 0) + b"AppImage"


def _appimage_with_metadata(payload_offset: int, **fields: str) -> bytes:
    parts = [b"Format=2\x00Type=Bundle\x00X-AppImage-Arch=x86_64\x00"]
    for key, value in fields.items():
        parts.append(f"{key}={value}".encode())
    parts.append(f"X-AppImage-Payload-Offset={payload_offset}".encode())
    parts.append(b"")  # trailing NUL
    metadata = b"\x00".join(parts)
    trailer_bytes = _make_trailer(metadata)
    # Prepend zeros so the payload starts at the declared offset.
    return b"\x00" * payload_offset + trailer_bytes


def test_reads_real_fixture_metadata(sample_appimage):
    meta = trailer.read_metadata_file(sample_appimage)
    assert meta.format == "2"
    assert meta.type == "Bundle"
    assert meta.arch == "x86_64"
    assert meta.path == "./sample.desktop"
    assert meta.payload_offset == 4096


def test_reads_crafted_metadata():
    data = _appimage_with_metadata(
        4096, **{"X-AppImage-Path": "./foo.desktop"}
    )
    meta = trailer.read_metadata(data)
    assert meta.format == "2"
    assert meta.type == "Bundle"
    assert meta.arch == "x86_64"
    assert meta.path == "./foo.desktop"
    assert meta.payload_offset == 4096
    assert meta.to_dict()["format"] == "2"


def test_payload_offset_roundtrips_when_not_page_aligned():
    data = _appimage_with_metadata(1234, **{"X-AppImage-Path": "a.desktop"})
    assert trailer.read_metadata(data).payload_offset == 1234


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00" * 10,
        b"this is definitely not an AppImage at all",
    ],
)
def test_rejects_non_appimages(data):
    with pytest.raises(trailer.TrailerError):
        trailer.read_metadata(data)


def test_rejects_truncated_trailer():
    # A valid magic but a trailer size that overruns the file.
    data = b"\x00" * 8 + b"AppImage" + b"\xff" * 32
    with pytest.raises(trailer.TrailerError):
        trailer.read_metadata(data)


def test_empty_metadata_still_parses():
    # A trailer with an (empty) .appimage section yields an empty mapping.
    meta = trailer.read_metadata(_make_trailer(b""))
    assert meta.raw == {}


def test_rejects_elf_without_appimage_section():
    # A real ELF trailer whose data section is named something else.
    trailer_bytes = _make_trailer(b"Format=2\x00", section_name=".other")
    with pytest.raises(trailer.TrailerError):
        trailer.read_metadata(trailer_bytes)


def test_reserved_field_is_read_but_ignored():
    data = _appimage_with_metadata(4096, **{"X-AppImage-Path": "x.desktop"})
    # The reserved field sits just before the magic; ensure parsing still works.
    assert trailer.read_metadata(data).payload_offset == 4096
