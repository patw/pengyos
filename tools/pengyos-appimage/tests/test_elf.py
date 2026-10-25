"""Tests for :mod:`pengyos_appimage.elf`.

The ELF objects are real ``gcc`` output compiled into the sample AppImage (see
``tests/fixtures/build_sample_appimage.py``), read through the squashfs reader.
An opt-in test compares the parser with ``objdump -p`` on host libraries.
"""

from __future__ import annotations

import glob
import os
import shutil
import struct
import subprocess

import pytest

from pengyos_appimage import elf, runtime, squashfs


@pytest.fixture
def payload(sample_appimage):
    meta = runtime.read_metadata_file(sample_appimage)
    with squashfs.SquashFS.open(sample_appimage, meta.payload_offset) as fs:
        yield fs


def test_pie_executable(payload):
    info = elf.parse(payload.read_path("usr/bin/sample"))
    assert info.elf_type == elf.ET_DYN
    assert info.machine == 0x3E
    assert info.needed == ["libbundled.so.1", "libc.so.6"]


def test_non_pie_executable_maps_strtab_through_load_segments(payload):
    # Linked at 0x400000: DT_STRTAB is a virtual address far beyond the file
    # size, so reading it as a file offset would find nothing.
    data = payload.read_path("usr/bin/sample-nopie")
    info = elf.parse(data)
    assert info.elf_type == elf.ET_EXEC
    assert info.needed == ["libm.so.6", "libc.so.6"]


def test_shared_library_soname(payload):
    info = elf.parse(payload.read_path("usr/lib/libbundled.so.1.0"))
    assert info.soname == "libbundled.so.1"
    assert info.needed == ["libc.so.6"]


def test_is_elf():
    assert elf.is_elf(b"\x7fELF\x02")
    assert not elf.is_elf(b"#!/bin/sh")


@pytest.mark.parametrize("data", [b"", b"\x7fELF", b"not an elf at all" * 4])
def test_rejects_non_elf(data):
    with pytest.raises(elf.ElfError):
        elf.parse(data)


def test_rejects_program_headers_past_the_end(payload):
    data = bytearray(payload.read_path("usr/bin/sample"))
    struct.pack_into("<Q", data, 32, len(data))  # e_phoff
    with pytest.raises(elf.ElfError, match="overruns"):
        elf.parse(bytes(data))


def test_static_object_needs_nothing(payload):
    data = bytearray(payload.read_path("usr/bin/sample"))
    struct.pack_into("<H", data, 56, 0)  # e_phnum = 0
    assert elf.parse(bytes(data)).needed == []


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT") or shutil.which("objdump") is None,
    reason="PENGYOS_IT=1 and objdump required",
)
def test_integration_matches_objdump():
    paths = sorted(glob.glob("/usr/lib/x86_64-linux-gnu/*.so.*"))[:200]
    paths += sorted(glob.glob("/usr/bin/*"))[:200]
    compared = 0
    for path in paths:
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if not elf.is_elf(data):
            continue
        out = subprocess.run(["objdump", "-p", path], capture_output=True, text=True).stdout
        ref = [l.split()[1] for l in out.splitlines() if l.strip().startswith("NEEDED")]
        assert elf.parse(data).needed == ref, path
        compared += 1
    assert compared > 50
