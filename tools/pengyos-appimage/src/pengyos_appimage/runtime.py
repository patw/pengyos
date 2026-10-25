"""Parse the AppImage *runtime* — the ELF at the front of the file.

A type-2 AppImage is two things concatenated: a small, usually static, ELF
executable (the runtime) followed by a squashfs payload::

    offset 0                         payload_offset
    | runtime ELF ... section table | squashfs ("hsqs") ... |

This module reads what we need **purely by parsing bytes**. It never mounts,
extracts, or executes anything:

* ``e_ident[8:11]`` holds the AppImage magic: ``b"AI\\x02"`` for type 2,
  ``b"AI\\x01"`` for the long-retired ISO 9660 type 1 (which we reject).
* The payload starts where the runtime ELF ends. The runtime computes that
  itself (``appimage_get_elf_size`` in AppImage/type2-runtime) as the larger of
  the end of the section-header table and the end of the last section; we do
  the same, then check the squashfs magic is really there.
* The runtime carries a few named sections: ``.upd_info`` (the update-info
  string, NUL-padded), ``.sha256_sig`` and ``.sig_key`` (embedded signature,
  all zeros when unsigned) and ``.digest_md5``.

There is no ``.desktop`` path in the runtime: the spec puts exactly one
``*.desktop`` at the payload root, and the callers look for it there.

Only the header, the section table and the few small sections are read, so a
200 MB AppImage costs a few kilobytes of I/O.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

ELF_MAGIC = b"\x7fELF"
APPIMAGE_TYPE1_MAGIC = b"AI\x01"
APPIMAGE_TYPE2_MAGIC = b"AI\x02"

SQUASHFS_MAGIC = b"hsqs"
# Newer type2-runtime builds can also carry a DwarFS payload; we cannot extract
# it, so name it instead of reporting a generic bad payload.
DWARFS_MAGIC = b"DWARFS"

# e_machine values worth naming (the runtime is built per architecture).
_MACHINES = {3: "i386", 0x28: "armhf", 0x3E: "x86_64", 0xB7: "aarch64"}

# Sections whose contents we report.
_UPDATE_SECTION = ".upd_info"
_SIGNATURE_SECTION = ".sha256_sig"

# Refuse absurd headers rather than allocating for them. A real runtime has a
# few dozen sections and is about 1 MB.
_MAX_SECTIONS = 4096
_MAX_SECTION_READ = 64 * 1024


@dataclass
class RuntimeInfo:
    """What the runtime header tells us about an AppImage."""

    #: AppImage type from the ``AI\\x0N`` magic. Always ``2`` here.
    type: int
    #: Runtime architecture from ``e_machine``, e.g. ``"x86_64"``.
    arch: str
    #: Byte offset where the squashfs payload begins.
    payload_offset: int
    #: The ``.upd_info`` string (e.g. ``gh-releases-zsync|...``), or ``None``.
    update_info: str | None = None
    #: ``True`` when ``.sha256_sig`` holds a signature (not all zeros).
    signed: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable view of the metadata."""
        return {
            "type": self.type,
            "arch": self.arch,
            "payload_offset": self.payload_offset,
            "update_info": self.update_info,
            "signed": self.signed,
        }


class AppImageFormatError(ValueError):
    """Raised when the file is not a type-2 AppImage we can read."""


def read_metadata_file(path: str | Path) -> RuntimeInfo:
    """Read the runtime header of the AppImage at *path*."""
    with Path(path).open("rb") as fh:
        return read_metadata(fh)


def read_metadata(fh: BinaryIO) -> RuntimeInfo:
    """Parse the runtime header from an open binary file.

    Raises:
        AppImageFormatError: If the file is not an ELF, carries no AppImage
            magic, is type 1, has a broken section table, or has no squashfs
            payload where the runtime ends.
    """
    fh.seek(0, 2)
    file_size = fh.tell()

    ident = _read_at(fh, 0, 16)
    if len(ident) < 16 or ident[:4] != ELF_MAGIC:
        raise AppImageFormatError("not an AppImage: no ELF runtime at offset 0")
    magic = ident[8:11]
    if magic == APPIMAGE_TYPE1_MAGIC:
        raise AppImageFormatError(
            "type-1 AppImage (ISO 9660 payload) is not supported"
        )
    if magic != APPIMAGE_TYPE2_MAGIC:
        raise AppImageFormatError(
            "not an AppImage: ELF has no AppImage magic (AI\\x02) in e_ident"
        )

    ei_class, ei_data = ident[4], ident[5]
    if ei_class not in (1, 2):
        raise AppImageFormatError(f"unknown ELF class {ei_class}")
    if ei_data not in (1, 2):
        raise AppImageFormatError(f"unknown ELF byte order {ei_data}")
    is64 = ei_class == 2
    endian = "<" if ei_data == 1 else ">"

    if is64:
        ehdr_fmt, ehdr_size = endian + "HHIQQQIHHHHHH", 48
        shdr_fmt, shdr_size = endian + "IIQQQQIIQQ", 64
    else:
        ehdr_fmt, ehdr_size = endian + "HHIIIIIHHHHHH", 36
        shdr_fmt, shdr_size = endian + "IIIIIIIIII", 40

    raw_ehdr = _read_at(fh, 16, ehdr_size)
    if len(raw_ehdr) < ehdr_size:
        raise AppImageFormatError("ELF header truncated")
    (
        _e_type, e_machine, _e_version, _e_entry, _e_phoff, e_shoff,
        _e_flags, _e_ehsize, _e_phentsize, _e_phnum, e_shentsize,
        e_shnum, e_shstrndx,
    ) = struct.unpack(ehdr_fmt, raw_ehdr)

    if e_shoff == 0 or e_shnum == 0:
        raise AppImageFormatError("runtime ELF has no section headers")
    if e_shnum > _MAX_SECTIONS:
        raise AppImageFormatError(f"implausible section count {e_shnum}")
    if e_shentsize < shdr_size:
        raise AppImageFormatError(f"unsupported section-header size {e_shentsize}")
    sht_end = e_shoff + e_shentsize * e_shnum
    if sht_end > file_size:
        raise AppImageFormatError("section-header table overruns the file")

    table = _read_at(fh, e_shoff, e_shentsize * e_shnum)
    # (name, type, offset, size) per section; field order differs only in width.
    sections = []
    for i in range(e_shnum):
        sh = struct.unpack_from(shdr_fmt, table, i * e_shentsize)
        sections.append((sh[0], sh[1], sh[4], sh[5]))

    # appimage_get_elf_size: max(end of section table, end of last section).
    _, _, last_off, last_size = sections[-1]
    payload_offset = max(sht_end, last_off + last_size)

    payload_magic = _read_at(fh, payload_offset, 6)
    if payload_magic.startswith(DWARFS_MAGIC):
        raise AppImageFormatError("DwarFS payload is not supported (squashfs only)")
    if not payload_magic.startswith(SQUASHFS_MAGIC):
        raise AppImageFormatError(
            f"no squashfs payload at offset {payload_offset} "
            "(where the runtime ELF ends)"
        )

    named = _section_names(fh, sections, e_shstrndx, file_size)
    update_info = None
    signed = False
    upd = named.get(_UPDATE_SECTION)
    if upd is not None:
        text = _read_section(fh, upd, file_size).split(b"\x00", 1)[0]
        update_info = text.decode("utf-8", "replace") or None
    sig = named.get(_SIGNATURE_SECTION)
    if sig is not None:
        signed = any(_read_section(fh, sig, file_size))

    return RuntimeInfo(
        type=2,
        arch=_MACHINES.get(e_machine, f"machine-{e_machine:#x}"),
        payload_offset=payload_offset,
        update_info=update_info,
        signed=signed,
    )


def _section_names(fh, sections, shstrndx, file_size) -> dict[str, tuple]:
    """Map section name -> (offset, size) via the section-name string table."""
    if shstrndx >= len(sections):
        return {}
    strtab = _read_section(fh, sections[shstrndx], file_size)
    out: dict[str, tuple] = {}
    for sh_name, _sh_type, off, size in sections:
        if sh_name >= len(strtab):
            continue
        end = strtab.find(b"\x00", sh_name)
        name = strtab[sh_name:end if end != -1 else None].decode("utf-8", "replace")
        out.setdefault(name, (sh_name, _sh_type, off, size))
    return out


def _read_section(fh, section, file_size) -> bytes:
    _, _, off, size = section
    if off >= file_size:
        return b""
    return _read_at(fh, off, min(size, _MAX_SECTION_READ, file_size - off))


def _read_at(fh, offset: int, size: int) -> bytes:
    fh.seek(offset)
    return fh.read(size)
