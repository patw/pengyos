"""Parse the AppImage *trailer* — the ELF section that carries the metadata.

An AppImage is a compressed filesystem payload (squashfs for the modern
"Type 2"/"Type 3" formats) with an ELF *trailer* appended to the end of the
file. That trailer holds a named ``.appimage`` section containing NUL-separated
``KEY=VALUE`` metadata records (``Format``, ``Type``, ``X-AppImage-Path``,
``X-AppImage-Payload-Offset``, ...).

This module reads that metadata **purely by parsing bytes**. It never mounts,
extracts, or executes the payload — reading the trailer is all we need to learn
the AppImage type and where its ``.desktop`` file lives.

The trailer layout (see https://spec.appimage.org/):

* The trailer is an ELF file located at the *end* of the AppImage.
* Its size is stored in a small struct at the very end of the file::

      struct { uint32 size; uint32 reserved; char magic[8]; }
                                                     ^^^^^^^^ == b"AppImage"

  The ``magic`` ("AppImage") is the last 8 bytes of a well-formed AppImage; we
  locate it by scanning the trailing bytes so we stay robust to the few bytes
  some toolchains leave after the magic.
* The ``size`` field (4 bytes, little-endian) sits 8 bytes before the magic and
  tells us where the trailer begins (``file_size - size``).
* Inside the trailer, the ``.appimage`` section (found by walking the ELF
  section-header table and resolving names through the section-name string
  table) contains the metadata.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# The magic that terminates a well-formed AppImage trailer.
TRAILER_MAGIC = b"AppImage"

# ELF header (64-bit, little-endian). Layout after the 16-byte e_ident:
#   e_type(H) e_machine(H) e_version(I) e_entry(Q) e_phoff(Q) e_shoff(Q)
#   e_flags(I) e_ehsize(H) e_phentsize(H) e_phnum(H) e_shentsize(H)
#   e_shnum(H) e_shstrndx(H)
_EHDR_FMT = "<HHIQQQIHHHHHH"
_EHDR_SIZE = 64

# ELF64 section header (64 bytes):
#   sh_name(I) sh_type(I) sh_flags(Q) sh_addr(Q) sh_offset(Q) sh_size(Q)
#   sh_link(I) sh_info(I) sh_addralign(Q) sh_entsize(Q)
_SHDR_FMT = "<IIQQQQIIQQ"
_SHDR_SIZE = 64

# Minimum sane trailer size: ELF header + one real section + the tail struct.
_MIN_TRAILER_SIZE = 64 + 32 + 16


@dataclass
class TrailerMetadata:
    """The subset of AppImage metadata we care about for inspection."""

    #: ``Format`` — the image format version, e.g. ``"2"`` or ``"3"``.
    format: str | None = None
    #: ``Type`` — the bundle type, e.g. ``"Bundle"``.
    type: str | None = None
    #: ``X-AppImage-Arch`` — target architecture.
    arch: str | None = None
    #: ``X-AppImage-Path`` — path (inside the payload) of the ``.desktop`` file.
    path: str | None = None
    #: ``X-AppImage-Payload-Offset`` — byte offset where the payload begins.
    payload_offset: int | None = None
    #: ``X-AppImage-Payload`` — payload size in bytes.
    payload_size: int | None = None
    #: ``X-AppImage-Root-Directory`` — root dir inside the payload.
    root_directory: str | None = None
    #: The raw ``KEY -> VALUE`` mapping for anything else found in the trailer.
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, str | None]:
        """Return a JSON-serialisable view of the metadata."""
        return {
            "format": self.format,
            "type": self.type,
            "arch": self.arch,
            "path": self.path,
            "payload_offset": self.payload_offset,
            "payload_size": self.payload_size,
            "root_directory": self.root_directory,
        }


class TrailerError(ValueError):
    """Raised when the AppImage trailer cannot be located or parsed."""


def _find_magic_end(data: bytes) -> int:
    """Return the offset of the trailing ``AppImage`` magic, or -1 if absent.

    We scan the final 64 bytes (which always contain the tail struct) and take
    the *last* occurrence, so stray copies of the magic earlier in the trailer
    body cannot confuse us.
    """
    window = data[-64:] if len(data) >= 64 else data
    base = len(data) - len(window)
    last = -1
    while True:
        idx = window.find(TRAILER_MAGIC, last + 1)
        if idx == -1:
            # Return the final valid occurrence (or -1 if there was none).
            return base + last
        last = idx


def read_metadata(data: bytes) -> TrailerMetadata:
    """Parse AppImage metadata from raw file bytes.

    Args:
        data: The full contents of the AppImage file.

    Returns:
        A :class:`TrailerMetadata` describing the AppImage.

    Raises:
        TrailerError: If the trailer magic, ELF header, or ``.appimage``
            section cannot be found.
    """
    if len(data) < _MIN_TRAILER_SIZE:
        raise TrailerError("file too small to be an AppImage")

    magic_off = _find_magic_end(data)
    if magic_off == -1:
        raise TrailerError("AppImage trailer magic 'AppImage' not found")

    # struct is { uint32 size; uint32 reserved; char magic[8] }
    size_off = magic_off - 8
    if size_off < 0:
        raise TrailerError("trailer struct truncated")
    trailer_size = struct.unpack_from("<I", data, size_off)[0]

    if trailer_size < _MIN_TRAILER_SIZE:
        raise TrailerError(f"suspicious trailer size {trailer_size}")

    trailer_start = len(data) - trailer_size
    if trailer_start < 0 or trailer_start + _MIN_TRAILER_SIZE > len(data):
        raise TrailerError("trailer size overruns file")

    if data[trailer_start:trailer_start + 4] != b"\x7fELF":
        raise TrailerError("trailer is not an ELF file")

    return _parse_elf_trailer(data, trailer_start)


def _parse_elf_trailer(data: bytes, trailer_start: int) -> TrailerMetadata:
    """Walk the ELF section-header table and read the ``.appimage`` section."""
    ehdr = data[trailer_start:trailer_start + _EHDR_SIZE]
    if len(ehdr) < _EHDR_SIZE:
        raise TrailerError("ELF header truncated")
    (
        _e_type, _e_machine, _e_version, _e_entry, _e_phoff, e_shoff,
        _e_flags, _e_ehsize, _e_phentsize, _e_phnum, e_shentsize,
        e_shnum, e_shstrndx,
    ) = struct.unpack_from(_EHDR_FMT, ehdr, 16)

    if e_shoff == 0 or e_shnum == 0:
        raise TrailerError("ELF has no section headers")
    if e_shentsize < _SHDR_SIZE:
        raise TrailerError(f"unsupported section-header size {e_shentsize}")

    base = trailer_start + e_shoff
    section_headers = [
        struct.unpack_from(_SHDR_FMT, data, base + i * e_shentsize)
        for i in range(e_shnum)
    ]

    # Resolve section names through the section-name string table.
    shstr_hdr = section_headers[e_shstrndx]
    shstr_off = shstr_hdr[4]
    shstr_size = shstr_hdr[5]
    shstr_data = data[
        trailer_start + shstr_off:trailer_start + shstr_off + shstr_size
    ]

    def _name(sh_name: int) -> str:
        end = shstr_data.find(b"\x00", sh_name)
        return shstr_data[sh_name:end].decode("utf-8", "replace")

    appimage = None
    for sh in section_headers:
        if _name(sh[0]) == ".appimage":
            appimage = sh
            break
    if appimage is None:
        raise TrailerError("'.appimage' section not found in trailer")

    off = appimage[4]
    length = appimage[5]
    raw = data[trailer_start + off:trailer_start + off + length]
    return _decode_metadata(raw)


def _decode_metadata(raw: bytes) -> TrailerMetadata:
    """Decode the ``KEY=VALUE\0KEY=VALUE\0`` metadata blob."""
    fields: dict[str, str] = {}
    for record in raw.split(b"\x00"):
        if not record:
            continue
        text = record.decode("utf-8", "replace")
        key, sep, value = text.partition("=")
        if sep:
            fields[key] = value

    meta = TrailerMetadata(
        format=fields.get("Format"),
        type=fields.get("Type"),
        arch=fields.get("X-AppImage-Arch"),
        path=fields.get("X-AppImage-Path"),
        payload_offset=_to_int(fields.get("X-AppImage-Payload-Offset")),
        payload_size=_to_int(fields.get("X-AppImage-Payload")),
        root_directory=fields.get("X-AppImage-Root-Directory"),
        raw=fields,
    )
    return meta


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        return None


def read_metadata_file(path: str | Path) -> TrailerMetadata:
    """Convenience wrapper that reads *path* and parses its trailer."""
    return read_metadata(Path(path).read_bytes())
