"""Tests for :mod:`pengyos_appimage.runtime`.

The committed fixture carries a real type-2 runtime copied from an upstream
AppImage (see ``tests/fixtures/build_sample_appimage.py``), so these tests check
the parser against what upstream ships. The negative cases are made by
corrupting a copy of that fixture, one field at a time.

An opt-in test parses any real AppImages named in ``PENGYOS_APPIMAGE_REAL``
(colon-separated paths) and checks the payload offset against the runtime's
own ``--appimage-offset``.
"""

from __future__ import annotations

import io
import os
import struct
import subprocess
from pathlib import Path

import pytest

from pengyos_appimage import runtime

# Measured on the fixture's runtime (AppImage/type2-runtime, commit 75849dc):
# the runtime's own `--appimage-offset` prints this number.
FIXTURE_PAYLOAD_OFFSET = 944632
FIXTURE_UPDATE_INFO = "zsync|https://example.invalid/sample-x86_64.AppImage.zsync"


@pytest.fixture
def fixture_bytes(sample_appimage: Path) -> bytearray:
    return bytearray(sample_appimage.read_bytes())


def _parse(data: bytes) -> runtime.RuntimeInfo:
    return runtime.read_metadata(io.BytesIO(bytes(data)))


def _section(data: bytes, wanted: str) -> tuple[int, int]:
    """Return (offset, size) of a named section in the fixture's runtime."""
    shoff, = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    hdrs = [
        struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize)
        for i in range(shnum)
    ]
    so, ss = hdrs[shstrndx][4], hdrs[shstrndx][5]
    strtab = bytes(data[so:so + ss])
    for h in hdrs:
        if strtab[h[0]:strtab.index(b"\x00", h[0])].decode() == wanted:
            return h[4], h[5]
    raise AssertionError(f"{wanted} not in fixture")


def test_reads_real_runtime(sample_appimage):
    meta = runtime.read_metadata_file(sample_appimage)
    assert meta.type == 2
    assert meta.arch == "x86_64"
    assert meta.payload_offset == FIXTURE_PAYLOAD_OFFSET
    assert meta.update_info == FIXTURE_UPDATE_INFO
    assert meta.signed is False


def test_payload_offset_points_at_squashfs(sample_appimage):
    meta = runtime.read_metadata_file(sample_appimage)
    with sample_appimage.open("rb") as fh:
        fh.seek(meta.payload_offset)
        assert fh.read(4) == b"hsqs"


def test_to_dict_is_json_shaped(sample_appimage):
    d = runtime.read_metadata_file(sample_appimage).to_dict()
    assert d == {
        "type": 2,
        "arch": "x86_64",
        "payload_offset": FIXTURE_PAYLOAD_OFFSET,
        "update_info": FIXTURE_UPDATE_INFO,
        "signed": False,
    }


def test_empty_update_info_is_none(fixture_bytes):
    off, size = _section(fixture_bytes, ".upd_info")
    fixture_bytes[off:off + size] = b"\x00" * size
    assert _parse(fixture_bytes).update_info is None


def test_nonzero_signature_reads_as_signed(fixture_bytes):
    off, _ = _section(fixture_bytes, ".sha256_sig")
    fixture_bytes[off:off + 4] = b"\x30\x82\x01\x0a"
    assert _parse(fixture_bytes).signed is True


@pytest.mark.parametrize(
    "data",
    [b"", b"\x00" * 10, b"this is definitely not an AppImage at all"],
)
def test_rejects_non_elf(data):
    with pytest.raises(runtime.AppImageFormatError, match="no ELF runtime"):
        _parse(data)


def test_rejects_plain_elf_without_magic(fixture_bytes):
    fixture_bytes[8:11] = b"\x00\x00\x00"
    with pytest.raises(runtime.AppImageFormatError, match="no AppImage magic"):
        _parse(fixture_bytes)


def test_rejects_type1(fixture_bytes):
    fixture_bytes[8:11] = b"AI\x01"
    with pytest.raises(runtime.AppImageFormatError, match="type-1"):
        _parse(fixture_bytes)


def test_rejects_section_table_overrun(fixture_bytes):
    struct.pack_into("<Q", fixture_bytes, 0x28, len(fixture_bytes))
    with pytest.raises(runtime.AppImageFormatError, match="overruns"):
        _parse(fixture_bytes)


def test_rejects_missing_section_table(fixture_bytes):
    struct.pack_into("<H", fixture_bytes, 0x3C, 0)  # e_shnum
    with pytest.raises(runtime.AppImageFormatError, match="no section headers"):
        _parse(fixture_bytes)


def test_rejects_runtime_without_payload(sample_appimage):
    # The runtime alone: a real AppImage ELF with nothing appended.
    head = sample_appimage.read_bytes()[:FIXTURE_PAYLOAD_OFFSET]
    with pytest.raises(runtime.AppImageFormatError, match="no squashfs payload"):
        _parse(head)


def test_names_dwarfs_payload(fixture_bytes):
    o = FIXTURE_PAYLOAD_OFFSET
    fixture_bytes[o:o + 6] = b"DWARFS"
    with pytest.raises(runtime.AppImageFormatError, match="DwarFS"):
        _parse(fixture_bytes)


def test_reads_only_the_header(sample_appimage, monkeypatch):
    """A 200 MB AppImage must not be slurped: reads stay small."""
    sizes: list[int] = []
    real = runtime._read_at

    def spy(fh, offset, size):
        sizes.append(size)
        return real(fh, offset, size)

    monkeypatch.setattr(runtime, "_read_at", spy)
    runtime.read_metadata_file(sample_appimage)
    assert max(sizes) <= runtime._MAX_SECTION_READ


def _real_appimages() -> list[str]:
    return [p for p in os.environ.get("PENGYOS_APPIMAGE_REAL", "").split(":") if p]


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT") or not _real_appimages(),
    reason="PENGYOS_IT=1 and PENGYOS_APPIMAGE_REAL=a.AppImage:b.AppImage required",
)
@pytest.mark.parametrize("path", _real_appimages())
def test_integration_offset_matches_runtime(path):
    """The runtime's own --appimage-offset agrees with the parser.

    This runs the AppImage's runtime (not its payload): only use AppImages you
    already trust to run.
    """
    meta = runtime.read_metadata_file(path)
    out = subprocess.run(
        [path, "--appimage-offset"], capture_output=True, text=True, check=True
    )
    assert int(out.stdout.strip()) == meta.payload_offset
