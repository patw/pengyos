"""Tests for :mod:`pengyos_debx.elf`.

Covers: Implement DT_NEEDED analysis via pure parsing of ELF headers (no ldd; ldd can
execute!).

The fixtures are built *in-test* with the standard library only — byte-exact
ELF64/ELF32, little/big-endian objects with a ``PT_DYNAMIC`` segment carrying
``DT_NEEDED`` entries. The parser is validated against real system libraries in
the verification ladder (``readelf``), and here against hand-built layouts so
the suite needs no network, root, or external tooling.
"""

import struct
from pathlib import Path

import pytest

from pengyos_debx import elf
from pengyos_debx.elf import (
    ElfFormatError,
    ElfInfo,
    parse_elf,
    parse_elf_file,
    scan_prefix,
    find_elf_files,
    is_elf,
)

# --- ELF constants reused by the builders ------------------------------------

ELFCLASS64, ELFCLASS32 = 2, 1
ELFDATA2LSB, ELFDATA2MSB = 1, 2
PT_DYNAMIC = 2
DT_NULL, DT_NEEDED, DT_STRTAB = 0, 1, 5
EM_X86_64 = 62


# --- ELF64 builder -----------------------------------------------------------


def _build_elf64(
    *,
    endian: str,
    machine: int = EM_X86_64,
    needed: list[str] | None = None,
    with_dynamic: bool = True,
) -> bytes:
    """Build a minimal but valid ELF64 object with a ``PT_DYNAMIC`` segment.

    ``endian`` is ``"<"`` or ``">"``. ``needed`` is the list of ``DT_NEEDED``
    library names. When ``with_dynamic`` is ``False`` the object has no dynamic
    segment (a relocatable/static-style object).
    """
    needed = needed or []
    # Layout: [ehdr(64)][phdr(56)][dynamic][strtab].
    phdr_off = 64
    dynamic_off = 64 + 56
    # Dynamic entries: 1 per needed lib + DT_STRTAB + DT_NULL.
    n_entries = len(needed) + 2
    dynamic_size = n_entries * 16
    strtab_off = dynamic_off + dynamic_size

    strtab = b"\x00"
    str_offsets = []
    for name in needed:
        str_offsets.append(len(strtab))
        strtab += name.encode() + b"\x00"

    # Program header (ELF64, 56 bytes).
    phdr = bytearray(56)
    struct.pack_into(endian + "I", phdr, 0, PT_DYNAMIC)  # p_type
    struct.pack_into(endian + "I", phdr, 4, 0x6)         # p_flags (RW)
    struct.pack_into(endian + "Q", phdr, 8, dynamic_off)  # p_offset
    struct.pack_into(endian + "Q", phdr, 16, dynamic_off)  # p_vaddr
    struct.pack_into(endian + "Q", phdr, 32, dynamic_size)  # p_filesz
    struct.pack_into(endian + "Q", phdr, 40, dynamic_size)  # p_memsz

    # Dynamic table.
    dyn = bytearray()
    for off in str_offsets:
        dyn += struct.pack(endian + "QQ", DT_NEEDED, off)
    dyn += struct.pack(endian + "QQ", DT_STRTAB, strtab_off)
    dyn += struct.pack(endian + "QQ", DT_NULL, 0)

    ehdr = bytearray(64)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = ELFCLASS64
    ehdr[5] = 1 if endian == "<" else 2  # EI_DATA
    ehdr[6] = 1
    struct.pack_into(endian + "H", ehdr, 0x12, machine)  # e_machine
    struct.pack_into(endian + "I", ehdr, 0x14, 1)        # e_version
    struct.pack_into(endian + "Q", ehdr, 0x20, phdr_off)  # e_phoff
    struct.pack_into(endian + "H", ehdr, 0x34, 64)        # e_ehsize
    struct.pack_into(endian + "H", ehdr, 0x36, 56)        # e_phentsize
    struct.pack_into(endian + "H", ehdr, 0x38, 1 if with_dynamic else 0)  # e_phnum

    blob = bytes(ehdr) + bytes(phdr)
    if not with_dynamic:
        return blob
    return blob + bytes(dyn) + strtab


# --- ELF32 builder -----------------------------------------------------------


def _build_elf32(*, endian: str, needed: list[str] | None = None) -> bytes:
    """Build a minimal ELF32 object with a ``PT_DYNAMIC`` segment."""
    needed = needed or []
    phdr_off = 52  # ELF32 ehdr is 52 bytes
    dynamic_off = phdr_off + 32  # one 32-byte phdr
    n_entries = len(needed) + 2
    dynamic_size = n_entries * 8
    strtab_off = dynamic_off + dynamic_size

    strtab = b"\x00"
    str_offsets = []
    for name in needed:
        str_offsets.append(len(strtab))
        strtab += name.encode() + b"\x00"

    phdr = bytearray(32)
    struct.pack_into(endian + "I", phdr, 0, PT_DYNAMIC)  # p_type
    struct.pack_into(endian + "I", phdr, 4, dynamic_off)  # p_offset
    struct.pack_into(endian + "I", phdr, 16, dynamic_size)  # p_filesz

    dyn = bytearray()
    for off in str_offsets:
        dyn += struct.pack(endian + "II", DT_NEEDED, off)
    dyn += struct.pack(endian + "II", DT_STRTAB, strtab_off)
    dyn += struct.pack(endian + "II", DT_NULL, 0)

    ehdr = bytearray(52)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = ELFCLASS32
    ehdr[5] = 1 if endian == "<" else 2
    ehdr[6] = 1
    struct.pack_into(endian + "H", ehdr, 0x12, EM_X86_64)  # e_machine
    struct.pack_into(endian + "I", ehdr, 0x18, 0)          # e_entry
    struct.pack_into(endian + "I", ehdr, 0x1c, phdr_off)   # e_phoff
    struct.pack_into(endian + "H", ehdr, 0x2a, 32)         # e_phentsize
    struct.pack_into(endian + "H", ehdr, 0x2c, 1)          # e_phnum

    return bytes(ehdr) + bytes(phdr) + bytes(dyn) + strtab


@pytest.fixture
def elf64_le():
    return _build_elf64(endian="<", needed=["libc.so.6", "libm.so.6"])


@pytest.fixture
def elf64_be():
    return _build_elf64(endian=">", needed=["libc.so.6"])


@pytest.fixture
def elf32_le():
    return _build_elf32(endian="<", needed=["libpthread.so.0", "libc.so.6"])


# --- magic / class / endian --------------------------------------------------


def test_parse_elf_bad_magic():
    with pytest.raises(ElfFormatError, match="magic"):
        parse_elf(b"\x00\x00\x00\x00not elf")


def test_parse_elf_too_short():
    with pytest.raises(ElfFormatError, match="magic"):
        parse_elf(b"\x7fEL")


def test_parse_elf_unknown_class():
    data = bytearray(_build_elf64(endian="<", needed=[]))
    data[4] = 9  # unknown class
    with pytest.raises(ElfFormatError, match="class"):
        parse_elf(bytes(data))


def test_elf64_class_endian_machine(elf64_le):
    info = parse_elf(elf64_le)
    assert info.elfclass == ELFCLASS64
    assert info.endian == "<"
    assert info.machine == EM_X86_64


def test_elf64_big_endian(elf64_be):
    info = parse_elf(elf64_be)
    assert info.elfclass == ELFCLASS64
    assert info.endian == ">"


def test_elf32_class(elf32_le):
    info = parse_elf(elf32_le)
    assert info.elfclass == ELFCLASS32
    assert info.endian == "<"


# --- DT_NEEDED extraction ----------------------------------------------------


def test_dt_needed_extracted(elf64_le):
    info = parse_elf(elf64_le)
    assert info.dt_needed == ["libc.so.6", "libm.so.6"]
    assert info.has_dynamic_section is True
    assert info.dynamic_offset is not None


def test_dt_needed_single(elf64_be):
    info = parse_elf(elf64_be)
    assert info.dt_needed == ["libc.so.6"]


def test_dt_needed_elf32(elf32_le):
    info = parse_elf(elf32_le)
    assert info.dt_needed == ["libpthread.so.0", "libc.so.6"]


def test_no_dynamic_section():
    blob = _build_elf64(endian="<", with_dynamic=False)
    info = parse_elf(blob)
    assert info.has_dynamic_section is False
    assert info.dynamic_offset is None
    assert info.dt_needed == []


def test_no_needed_entries():
    blob = _build_elf64(endian="<", needed=[])
    info = parse_elf(blob)
    assert info.dt_needed == []
    assert info.has_dynamic_section is True


def test_dt_needed_case_sensitive():
    blob = _build_elf64(endian="<", needed=["libc.so.6", "LibC.So.6"])
    info = parse_elf(blob)
    assert info.dt_needed == ["libc.so.6", "LibC.So.6"]


def test_str_format_contains_needed():
    info = parse_elf(_build_elf64(endian="<", needed=["libc.so.6"]))
    text = str(info)
    assert "ELF64" in text
    assert "libc.so.6" in text


# --- file / prefix scanning --------------------------------------------------


def test_parse_elf_file(tmp_path: Path, elf64_le):
    p = tmp_path / "libfoo.so.1"
    p.write_bytes(elf64_le)
    info = parse_elf_file(str(p))
    assert info.dt_needed == ["libc.so.6", "libm.so.6"]


def test_is_elf():
    assert is_elf(b"\x7fELF\x02\x01\x01\x00") is True
    assert is_elf(b"not elf") is False
    assert is_elf(b"\x7fEL") is False


def test_find_elf_files(tmp_path: Path):
    root = tmp_path / "rootfs"
    root.mkdir()
    (root / "usr").mkdir()
    (root / "usr" / "lib").mkdir()
    (root / "usr" / "lib" / "liba.so.1").write_bytes(_build_elf64(endian="<"))
    (root / "usr" / "lib" / "libb.so.1").write_bytes(_build_elf32(endian="<"))
    # A non-ELF file that merely ends in .so must be skipped.
    (root / "usr" / "lib" / "notreal.so").write_text("hello")
    # A plain text file.
    (root / "README").write_text("docs")
    found = sorted(find_elf_files(str(root)))
    assert found == [
        str(root / "usr" / "lib" / "liba.so.1"),
        str(root / "usr" / "lib" / "libb.so.1"),
    ]


def test_scan_prefix_yields_info(tmp_path: Path):
    root = tmp_path / "rootfs"
    root.mkdir()
    (root / "liba.so").write_bytes(_build_elf64(endian="<", needed=["libc.so.6"]))
    (root / "libb.so").write_bytes(_build_elf32(endian="<", needed=["libm.so.6"]))
    results = dict(scan_prefix(str(root)))
    assert set(results) == {
        str(root / "liba.so"),
        str(root / "libb.so"),
    }
    assert results[str(root / "liba.so")].dt_needed == ["libc.so.6"]
    assert results[str(root / "libb.so")].dt_needed == ["libm.so.6"]


def test_scan_prefix_skips_unparseable(tmp_path: Path):
    root = tmp_path / "rootfs"
    root.mkdir()
    good = root / "good.so"
    good.write_bytes(_build_elf64(endian="<", needed=["libc.so.6"]))
    # A file with ELF magic but a truncated header is skipped with a warning.
    (root / "broken.so").write_bytes(b"\x7fELF\x02\x01")
    results = list(scan_prefix(str(root)))
    assert [name for name, _ in results] == [str(good)]


# --- security invariant ------------------------------------------------------


def test_parse_never_shells_out(
    elf64_le, elf32_le, monkeypatch, tmp_path: Path
):
    """Parsing ELF headers must never invoke an external process (no ldd, no
    dpkg-deb). We stub ``subprocess.run`` and assert it is never called."""
    import subprocess as _subprocess

    calls = []

    def _fake_run(*args, **kwargs):
        calls.append(args)
        raise AssertionError("elf parsing must not shell out (no ldd)")

    monkeypatch.setattr(_subprocess, "run", _fake_run)

    # Also exercise the file/prefix entry points.
    p = tmp_path / "lib.so"
    p.write_bytes(elf64_le)
    parse_elf_file(str(p))
    parse_elf(elf32_le)
    assert calls == []
