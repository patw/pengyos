"""Read an ELF object's ``DT_NEEDED`` list from bytes — never ``ldd``.

``ldd`` runs the dynamic loader against the file, which can execute code from
it. ``check`` reads the same answer out of the headers instead:

1. the program headers give ``PT_DYNAMIC`` (where the dynamic table is in the
   file) and the ``PT_LOAD`` segments (how virtual addresses map to file
   offsets);
2. ``DT_STRTAB`` in the dynamic table is a *virtual address*, so it is mapped
   through the ``PT_LOAD`` segments to a file offset;
3. each ``DT_NEEDED`` value is an offset into that string table.

Step 2 matters: in a shared library the first segment usually maps at address
0, so treating the address as a file offset happens to work, but in a non-PIE
executable (linked at 0x400000) it reads garbage.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

ELF_MAGIC = b"\x7fELF"

PT_LOAD = 1
PT_DYNAMIC = 2
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_SONAME = 14

ET_EXEC = 2
ET_DYN = 3

_MAX_PHDRS = 512
_MAX_DYNAMIC = 4096


class ElfError(ValueError):
    """Raised when bytes are not an ELF object we can read."""


@dataclass
class ElfInfo:
    """What ``check`` needs from one ELF object."""

    machine: int
    elf_type: int
    needed: list[str] = field(default_factory=list)
    soname: str | None = None


def is_elf(head: bytes) -> bool:
    return head[:4] == ELF_MAGIC


def parse(data: bytes) -> ElfInfo:
    """Parse *data* (a whole ELF file).

    Raises:
        ElfError: If the header or a table it points at is malformed.
    """
    if len(data) < 52 or not is_elf(data):
        raise ElfError("not an ELF object")
    ei_class, ei_data = data[4], data[5]
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        raise ElfError("unknown ELF class or byte order")
    e = "<" if ei_data == 1 else ">"
    is64 = ei_class == 2
    if is64:
        if len(data) < 64:
            raise ElfError("ELF header truncated")
        e_type, e_machine = struct.unpack_from(e + "HH", data, 16)
        e_phoff, = struct.unpack_from(e + "Q", data, 32)
        e_phentsize, e_phnum = struct.unpack_from(e + "HH", data, 54)
        word, dyn_fmt = "Q", e + "qQ"
    else:
        e_type, e_machine = struct.unpack_from(e + "HH", data, 16)
        e_phoff, = struct.unpack_from(e + "I", data, 28)
        e_phentsize, e_phnum = struct.unpack_from(e + "HH", data, 42)
        word, dyn_fmt = "I", e + "iI"

    info = ElfInfo(machine=e_machine, elf_type=e_type)
    if e_phnum == 0:
        return info
    if e_phnum > _MAX_PHDRS or e_phentsize < (56 if is64 else 32):
        raise ElfError("implausible program-header table")
    if e_phoff + e_phnum * e_phentsize > len(data):
        raise ElfError("program-header table overruns the file")

    loads: list[tuple[int, int, int]] = []  # (vaddr, offset, filesz)
    dynamic: tuple[int, int] | None = None  # (offset, filesz)
    for i in range(e_phnum):
        base = e_phoff + i * e_phentsize
        p_type, = struct.unpack_from(e + "I", data, base)
        if is64:
            p_offset, p_vaddr = struct.unpack_from(e + "QQ", data, base + 8)
            p_filesz, = struct.unpack_from(e + "Q", data, base + 32)
        else:
            p_offset, p_vaddr = struct.unpack_from(e + "II", data, base + 4)
            p_filesz, = struct.unpack_from(e + "I", data, base + 16)
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC and dynamic is None:
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        return info  # static: needs nothing

    entry = struct.calcsize(dyn_fmt)
    off, size = dynamic
    end = min(off + size, len(data), off + _MAX_DYNAMIC * entry)
    strtab_vaddr = None
    needed_offs: list[int] = []
    soname_off = None
    pos = off
    while pos + entry <= end:
        tag, val = struct.unpack_from(dyn_fmt, data, pos)
        pos += entry
        if tag == DT_NULL:
            break
        if tag == DT_NEEDED:
            needed_offs.append(val)
        elif tag == DT_STRTAB:
            strtab_vaddr = val
        elif tag == DT_SONAME:
            soname_off = val
    if strtab_vaddr is None:
        return info
    strtab = _vaddr_to_offset(loads, strtab_vaddr)
    if strtab is None:
        raise ElfError("DT_STRTAB is outside every loaded segment")

    info.needed = [_cstring(data, strtab + o) for o in needed_offs]
    info.needed = [n for n in info.needed if n]
    if soname_off is not None:
        info.soname = _cstring(data, strtab + soname_off) or None
    return info


def _vaddr_to_offset(loads, vaddr: int) -> int | None:
    for p_vaddr, p_offset, p_filesz in loads:
        if p_vaddr <= vaddr < p_vaddr + p_filesz:
            return vaddr - p_vaddr + p_offset
    return None


def _cstring(data: bytes, start: int) -> str:
    if start >= len(data):
        return ""
    end = data.find(b"\x00", start, start + 4096)
    if end == -1:
        return ""
    return data[start:end].decode("utf-8", "surrogateescape")
