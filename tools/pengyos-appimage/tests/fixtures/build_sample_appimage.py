#!/usr/bin/env python3
"""Build the committed sample AppImage fixture used by the test suite.

This is a *build-time* helper (not part of the runtime package). It assembles a
tiny, standards-shaped AppImage:

* a squashfs payload (built with ``mksquashfs``) containing a poison ``AppRun``
  hook, a ``.desktop`` file, an icon, and a payload binary;
* an ELF trailer carrying ``.appimage`` metadata.

The poison ``AppRun`` would create ``/tmp/PENGYOS_APPIMAGE_POISON`` if it were
ever executed. The suite asserts it is *never* executed.

Usage::

    python3 tests/fixtures/build_sample_appimage.sh   # or run this file
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FIXTURE = FIXTURES / "sample-appimage"

# The poison marker the embedded AppRun would create if ever executed.
POISON_PATH = "/tmp/PENGYOS_APPIMAGE_POISON"


def _png_1x1() -> bytes:
    """Return a minimal valid 1x1 PNG (enough for an icon fixture)."""
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data) & 0xFFFFFFFF
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = bytes([0, 0, 0, 0, 3, 0, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _build_trailer(metadata: bytes) -> bytes:
    """Return an ELF trailer with a single ``.appimage`` section."""
    elf64_ehdr = 64
    elf64_shdr = 64

    def shdr(sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size,
             sh_link, sh_info, sh_addralign, sh_entsize):
        return struct.pack(
            "<IIQQQQIIQQ", sh_name, sh_type, sh_flags, sh_addr, sh_offset,
            sh_size, sh_link, sh_info, sh_addralign, sh_entsize
        )

    shstr = b"\x00.sample.desktop\x00.appimage\x00.shstrtab\x00"
    name_desktop = 1
    name_appimage = 1 + len(b".sample.desktop\x00")
    name_shstrtab = 1 + len(b".sample.desktop\x00.appimage\x00")

    mlen = len(metadata)
    slen = len(shstr)
    appimage_off = elf64_ehdr
    shstrtab_off = elf64_ehdr + mlen
    shoff = elf64_ehdr + mlen + slen

    shdrs = (
        shdr(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        + shdr(name_appimage, 1, 2, 0, appimage_off, mlen, 0, 0, 1, 0)
        + shdr(name_shstrtab, 3, 0, 0, shstrtab_off, slen, 0, 0, 1, 0)
    )

    e_ident = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
    ehdr = e_ident + struct.pack(
        "<HHIQQQIHHHHHH",
        2, 0x3E, 1, 0, 0, shoff, 0, elf64_ehdr, 0, 0, elf64_shdr, 3, 2,
    )
    body = ehdr + metadata + shstr + shdrs
    trailer_size = len(body) + 16
    return body + struct.pack("<II", trailer_size, 0) + b"AppImage"


def build(output: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        appdir = tmp / "appdir"
        (appdir / "usr" / "bin").mkdir(parents=True)
        (appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps").mkdir(
            parents=True
        )

        # Poison AppRun: would create POISON_PATH if ever executed.
        (appdir / "AppRun").write_text(
            "#!/bin/sh\n"
            f'echo "poisoned" > "{POISON_PATH}"\n'
            'exec ./usr/bin/sample "$@"\n',
            encoding="utf-8",
        )
        (appdir / "AppRun").chmod(0o755)

        # Payload binary (a harmless echo wrapper).
        (appdir / "usr" / "bin" / "sample").write_text(
            "#!/bin/sh\necho sample\n", encoding="utf-8"
        )
        (appdir / "usr" / "bin" / "sample").chmod(0o755)

        # Desktop entry.
        (appdir / "sample.desktop").write_text(
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Sample App\n"
            "GenericName=Sample\n"
            f"Exec=sample %F\n"
            "Icon=sample\n"
            "Terminal=false\n"
            "Categories=Utility;\n",
            encoding="utf-8",
        )

        # Icon.
        (appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
         / "sample.png").write_bytes(_png_1x1())
        (appdir / ".DirIcon").write_bytes(_png_1x1())

        # Squashfs payload.
        squashfs = tmp / "payload.squashfs"
        subprocess.run(
            ["mksquashfs", str(appdir), str(squashfs), "-no-xattrs", "-comp", "xz"],
            check=True,
        )
        sq_size = squashfs.stat().st_size
        # Round the payload offset up to a 4 KiB page boundary.
        off = ((sq_size + 4095) // 4096) * 4096

        # Trailer whose payload offset matches the squashfs location.
        metadata = (
            b"Format=2\x00Type=Bundle\x00X-AppImage-Arch=x86_64\x00"
            b"X-AppImage-Path=./sample.desktop\x00"
            b"X-AppImage-Payload-Offset=%d\x00" % off
        )
        trailer = _build_trailer(metadata)

        output.parent.mkdir(parents=True, exist_ok=True)
        # Pad to ``off`` (page-aligned payload), then squashfs, then trailer.
        with output.open("wb") as fh:
            fh.write(b"\x00" * off)
            fh.write(squashfs.read_bytes())
            fh.write(trailer)


def main() -> int:
    build(FIXTURE)
    print(f"wrote {FIXTURE} ({FIXTURE.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
