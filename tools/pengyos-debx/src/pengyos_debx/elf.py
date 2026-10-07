"""Pure ELF header parsing — extract ``DT_NEEDED`` without running anything.

This module reads the dynamic dependencies of an ELF object (executable, shared
object or object file) by parsing the ELF headers directly with :mod:`struct`.
It **never** invokes ``ldd`` (which runs the ELF's interpreter and can execute
code) and it never executes the file — reading a header is a pure memory
operation.

Why pure parsing? ``ldd`` is the usual way to see what a shared object needs,
but it *executes* the dynamic linker against the file, which can run arbitrary
code. For a tool whose whole point is to never run package scripts (and never
run the package's own binaries), the dependency scan must be read-only. Parsing
the ELF headers gives us the same ``DT_NEEDED`` information without any
execution.

Public surface:

* :class:`ElfInfo`        -- the parsed result of one ELF object.
* :func:`parse_elf`       -- parse an ELF blob (``bytes``) in memory.
* :func:`parse_elf_file`  -- read a path and parse it.
* :func:`find_elf_files`  -- walk a prefix collecting ELF files by magic.

The parser is deliberately *structural*: it understands just enough of the ELF
32/64-bit, little/big-endian layouts to locate the ``PT_DYNAMIC`` program
segment, walk its ``d_tag`` entries, and read the ``DT_NEEDED`` strings from the
dynamic string table. It does not model every ELF field — only what is needed
to answer "what libraries does this object need?".
"""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

# --- ELF constants (elf.h) ---------------------------------------------------

_EI_NIDENT = 16
ELFCLASS32 = 1
ELFCLASS64 = 2
ELFDATA2LSB = 1  # little-endian
ELFDATA2MSB = 2  # big-endian

# Program header ``p_type`` values.
PT_DYNAMIC = 2

# Dynamic table ``d_tag`` values.
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5

# The e_ident class/data live at fixed offsets inside the 16-byte EI_IDENT.
_EI_CLASS = 4
_EI_DATA = 5

# ``DT_NEEDED`` string lookups are case-sensitive (the loader matches the exact
# soname). We do not fold case here.


class ElfFormatError(Exception):
    """Raised when the bytes are not a parseable ELF object."""


@dataclass(frozen=True)
class ElfInfo:
    """The result of parsing one ELF object.

    Attributes:
        elfclass: the on-disk ``EI_CLASS`` value — ``1`` (ELF32) or ``2``
            (ELF64).
        endian: ``"<"`` (little-endian) or ``">"`` (big-endian).
        machine: the ``e_machine`` value (see ``elf.h``; e.g. ``62`` for
            ``EM_X86_64``).
        entry: the ``e_entry`` value.
        dt_needed: the list of ``DT_NEEDED`` library names this object requires.
        dynamic_offset: the file offset of the ``PT_DYNAMIC`` segment, or
            ``None`` when the object has no dynamic segment.
        has_dynamic_section: whether a ``PT_DYNAMIC`` segment was found.
    """

    elfclass: int
    endian: str
    machine: int
    entry: int
    dt_needed: List[str] = field(default_factory=list)
    dynamic_offset: Optional[int] = None
    has_dynamic_section: bool = False

    def __str__(self) -> str:
        kind = "ELF64" if self.elfclass == ELFCLASS64 else "ELF32"
        return (
            f"{kind} {self.endian}-endian machine={self.machine} "
            f"needs={self.dt_needed}"
        )


def _u(fmt_suffix: str, data: bytes, off: int, endian: str):
    """``struct.unpack`` with an embedded endian prefix, range-checked.

    NOTE: this helper must never be used to read a *sequence* of adjacent
    fields — ``struct`` inserts alignment padding between fields, which silently
    desynchronises a multi-field format. Callers that need several fields read
    them individually at explicit byte offsets (see ``_parse_elf32``/
    ``_parse_elf64``), where the offsets are pinned to the on-disk layout.
    """
    fmt = endian + fmt_suffix
    return struct.unpack(fmt, data[off : off + struct.calcsize(fmt)])


def _uoff(data: bytes, off: int, size: int, endian: str):
    """Read a fixed-width unsigned integer of ``size`` (1/2/4/8) at ``off``.

    Reading by explicit byte size avoids ``struct``'s field-alignment padding
    entirely, so multi-field header layouts stay synchronised.
    """
    fmt = {"1": "B", "2": "H", "4": "I", "8": "Q"}[str(size)]
    return struct.unpack(endian + fmt, data[off : off + size])[0]


def parse_elf(data: bytes) -> ElfInfo:
    """Parse an ELF object held in ``data`` (``bytes``).

    Raises:
        ElfFormatError: if the bytes are not a well-formed ELF object.
    """
    if len(data) < _EI_NIDENT or data[:4] != b"\x7fELF":
        raise ElfFormatError("not an ELF object (bad magic)")
    ei_class = data[_EI_CLASS]
    if ei_class == ELFCLASS64:
        endian = ">" if data[_EI_DATA] == ELFDATA2MSB else "<"
        return _parse_elf64(data, endian)
    if ei_class == ELFCLASS32:
        endian = ">" if data[_EI_DATA] == ELFDATA2MSB else "<"
        return _parse_elf32(data, endian)
    raise ElfFormatError(f"unknown ELF class {ei_class}")


def _parse_elf32(data: bytes, endian: str) -> ElfInfo:
    # Fixed offsets inside the ELF32 header (all after the 16-byte e_ident):
    #   e_machine(H)@0x12  e_entry(I)@0x18  e_phoff(I)@0x1c
    #   e_phentsize(H)@0x2a  e_phnum(H)@0x2c
    machine = _uoff(data, 0x12, 2, endian)
    entry = _uoff(data, 0x18, 4, endian)
    e_phoff = _uoff(data, 0x1C, 4, endian)
    e_phentsize = _uoff(data, 0x2A, 2, endian)
    e_phnum = _uoff(data, 0x2C, 2, endian)
    return _walk_dynamic(
        data, endian, e_phoff, e_phentsize, e_phnum, elfclass=ELFCLASS32,
        machine=machine, entry=entry,
    )


def _parse_elf64(data: bytes, endian: str) -> ElfInfo:
    # Fixed offsets inside the ELF64 header (all after the 16-byte e_ident):
    #   e_machine(H)@0x12  e_entry(Q)@0x18  e_phoff(Q)@0x20
    #   e_phentsize(H)@0x36  e_phnum(H)@0x38
    machine = _uoff(data, 0x12, 2, endian)
    entry = _uoff(data, 0x18, 8, endian)
    e_phoff = _uoff(data, 0x20, 8, endian)
    e_phentsize = _uoff(data, 0x36, 2, endian)
    e_phnum = _uoff(data, 0x38, 2, endian)
    return _walk_dynamic(
        data, endian, e_phoff, e_phentsize, e_phnum, elfclass=ELFCLASS64,
        machine=machine, entry=entry,
    )


def _walk_dynamic(
    data: bytes,
    endian: str,
    e_phoff: int,
    phentsize: int,
    phnum: int,
    *,
    elfclass: int,
    machine: int,
    entry: int,
) -> ElfInfo:
    """Locate ``PT_DYNAMIC`` among the program headers and read its entries."""
    ph_entry_size = 32 if elfclass == ELFCLASS32 else 56
    dynamic_off = None
    dynamic_size = None
    for i in range(phnum):
        base = e_phoff + phentsize * i
        if base + ph_entry_size > len(data):
            break
        if elfclass == ELFCLASS32:
            # 32-byte phdr: p_type(4) p_offset(4) p_vaddr(4) p_paddr(4)
            # p_filesz(4) p_memsz(4) p_flags(4) p_align(4).
            (p_type,) = _u("I", data, base, endian)
            (p_offset,) = _u("I", data, base + 4, endian)
            (p_filesz,) = _u("I", data, base + 16, endian)
        else:
            # ELF64 phdr: p_type(4) p_flags(4) p_offset(8) p_vaddr(8)
            # p_paddr(8) p_filesz(8) p_memsz(8) p_align(8).
            (p_type,) = _u("I", data, base, endian)
            (p_offset,) = _u("Q", data, base + 8, endian)
            (p_filesz,) = _u("Q", data, base + 32, endian)
        if p_type != PT_DYNAMIC:
            continue
        dynamic_off = p_offset
        dynamic_size = p_filesz
        break

    if dynamic_off is None:
        # No dynamic segment: nothing to need. This is fine for a static or
        # relocatable object; report an empty dependency list.
        return ElfInfo(elfclass, endian, machine=machine, entry=entry)

    entries = _read_dynamic_entries(data, endian, dynamic_off, dynamic_size)
    dt_needed = _collect_needed(data, endian, entries)
    return ElfInfo(
        elfclass,
        endian,
        machine=machine,
        entry=entry,
        dt_needed=dt_needed,
        dynamic_offset=dynamic_off,
        has_dynamic_section=True,
    )


def _read_dynamic_entries(
    data: bytes, endian: str, off: int, size: int
) -> List[Tuple[int, int]]:
    """Yield ``(d_tag, d_val)`` pairs from a ``PT_DYNAMIC`` segment.

    A dynamic entry is a ``d_tag`` followed by a ``d_val``/``d_ptr``. For ELF32
    both are 4-byte words (8-byte entries); for ELF64 both are 8-byte words
    (``Elf64_Sxword`` / ``Elf64_Xword``, 16-byte entries). The table is
    NUL-terminated (``d_tag`` ``DT_NULL``); we stop at the first NUL entry or at
    the segment boundary.
    """
    elfclass = data[4]
    if elfclass == ELFCLASS32:
        d_tag_fmt, d_val_fmt = "I", "I"
    else:
        d_tag_fmt, d_val_fmt = "Q", "Q"
    entries: List[Tuple[int, int]] = []
    pos = off
    end = off + size
    entry_size = struct.calcsize(endian + d_tag_fmt + d_val_fmt)
    while pos + entry_size <= end:
        (d_tag,) = _u(d_tag_fmt, data, pos, endian)
        if d_tag == DT_NULL:
            break
        (d_val,) = _u(d_val_fmt, data, pos + struct.calcsize(endian + d_tag_fmt),
                      endian)
        entries.append((d_tag, d_val))
        pos += entry_size
    return entries


def _collect_needed(
    data: bytes, endian: str, entries: List[Tuple[int, int]]
) -> List[str]:
    """Given ``(d_tag, d_val)`` pairs, resolve ``DT_NEEDED`` to library names."""
    strtab_off = None
    needed: List[int] = []
    for d_tag, d_val in entries:
        if d_tag == DT_STRTAB:
            strtab_off = d_val
        elif d_tag == DT_NEEDED:
            needed.append(d_val)
    if strtab_off is None:
        return []
    names: List[str] = []
    for off in needed:
        name = _read_cstring(data, strtab_off + off)
        if name:
            names.append(name)
    return names


def _read_cstring(data: bytes, start: int) -> str:
    """Read a NUL-terminated C string at ``data[start:]``."""
    end = data.find(b"\x00", start)
    if end == -1:
        end = len(data)
    return data[start:end].decode("utf-8", "surrogateescape")


def parse_elf_file(path: str) -> ElfInfo:
    """Read ``path`` and parse it as an ELF object."""
    with open(path, "rb") as fh:
        data = fh.read()
    return parse_elf(data)


def is_elf(data: bytes) -> bool:
    """Return ``True`` if ``data`` begins with the ELF magic."""
    return len(data) >= 4 and data[:4] == b"\x7fELF"


def find_elf_files(root: str) -> Iterator[str]:
    """Yield the paths of ELF objects under ``root`` (by magic, not extension).

    This walks the prefix and reads only the leading 4 bytes of each regular
    file to check for the ELF magic — it does not treat ``.so``/``.a``/``.o``
    by name, so a misnamed ELF is still detected and a non-ELF ``.so`` is
    skipped. Symlinks are not followed and directories are not executed.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        # Do not descend into Python scratch dirs.
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            try:
                with open(full, "rb") as fh:
                    if is_elf(fh.read(4)):
                        yield full
            except OSError:
                # Unreadable file: skip rather than aborting the whole walk.
                continue


def scan_prefix(root: str) -> Iterator[tuple[str, ElfInfo]]:
    """Yield ``(path, ElfInfo)`` for every ELF object under ``root``.

    Each file is read and parsed with :func:`parse_elf_file`; files that are not
    parseable ELF objects are skipped (with a warning on ``stderr``) rather than
    aborting the scan, so a prefix with a stray non-ELF file still yields the
    ELF objects it does contain. Nothing here is executed — only headers are
    parsed.
    """
    for path in find_elf_files(root):
        try:
            yield path, parse_elf_file(path)
        except ElfFormatError as exc:
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
            continue
