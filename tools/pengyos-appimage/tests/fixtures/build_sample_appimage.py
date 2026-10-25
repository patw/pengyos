#!/usr/bin/env python3
"""Build the committed sample AppImage fixture used by the test suite.

This is a *build-time* helper (not part of the runtime package). It assembles a
tiny AppImage the way ``appimagetool`` does, from a **real** type-2 runtime:

* the runtime ELF is copied byte for byte from an existing AppImage (its first
  ``payload_offset`` bytes), so the parser is tested against what upstream
  actually ships, not against a layout we wrote ourselves;
* its ``.upd_info`` section is filled in, as ``appimagetool -u`` does, so the
  update-info path is exercised;
* a squashfs payload (built with ``mksquashfs``) is appended, containing a
  poison ``AppRun`` hook, a root ``.desktop`` file, an icon, and small ELF
  objects compiled here for ``check``:

  - ``usr/bin/sample`` (PIE) needs the bundled ``libbundled.so.1``, which ships
    as ``usr/lib/libbundled.so.1.0`` plus a ``libbundled.so.1`` symlink;
  - ``usr/bin/sample-nopie`` is linked at 0x400000, so its ``DT_STRTAB`` address
    is not a file offset (the case a naive parser gets wrong); needs libm;
  - ``usr/bin/needs-missing`` needs ``libpengyos-absent.so.3``, which is not
    shipped anywhere;
  - ``usr/lib/libQt6XcbQpa.so.6`` is a stand-in with that soname, so the
    dlopen table reports ``libxcb-cursor.so.0``.

The poison ``AppRun`` would create ``/tmp/PENGYOS_APPIMAGE_POISON`` if it were
ever executed. The suite asserts it is *never* executed.

The type2-runtime (github.com/AppImage/type2-runtime) is MIT licensed.

Usage::

    python3 tests/fixtures/build_sample_appimage.py SOME-REAL.AppImage

Needs ``gcc`` and ``mksquashfs``.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
FIXTURE = FIXTURES / "sample-appimage"

sys.path.insert(0, str(REPO_ROOT / "src"))
from pengyos_appimage import runtime  # noqa: E402

# The poison marker the embedded AppRun would create if ever executed.
POISON_PATH = "/tmp/PENGYOS_APPIMAGE_POISON"

# What the fixture's .upd_info carries (the tests assert it round-trips).
UPDATE_INFO = b"zsync|https://example.invalid/sample-x86_64.AppImage.zsync"


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


def _extract_runtime(appimage: Path) -> bytearray:
    """Return the runtime ELF of *appimage* (everything before the payload)."""
    meta = runtime.read_metadata_file(appimage)
    with appimage.open("rb") as fh:
        return bytearray(fh.read(meta.payload_offset))


def _set_update_info(elf: bytearray, info: bytes) -> None:
    """Write *info* into the runtime's ``.upd_info`` section (64-bit LE)."""
    shoff, = struct.unpack_from("<Q", elf, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", elf, 0x3A)
    headers = [
        struct.unpack_from("<IIQQQQIIQQ", elf, shoff + i * shentsize)
        for i in range(shnum)
    ]
    str_off, str_size = headers[shstrndx][4], headers[shstrndx][5]
    strtab = bytes(elf[str_off:str_off + str_size])
    for sh in headers:
        name = strtab[sh[0]:strtab.index(b"\x00", sh[0])]
        if name == b".upd_info":
            off, size = sh[4], sh[5]
            if len(info) >= size:
                raise SystemExit(".upd_info section too small")
            elf[off:off + size] = info.ljust(size, b"\x00")
            return
    raise SystemExit("runtime has no .upd_info section")


_CFLAGS = ["-O2", "-s", "-Wl,--no-as-needed", "-Wl,--build-id=none"]


def _cc(args: list[str], cwd: Path) -> None:
    subprocess.run(["gcc", *_CFLAGS, *args], cwd=cwd, check=True)


def _compile_elves(work: Path, appdir: Path) -> None:
    """Compile the ELF objects described in the module docstring."""
    (work / "lib.c").write_text("int pengyos_fixture(void) { return 7; }\n")
    (work / "main.c").write_text(
        "int pengyos_fixture(void);\nint main(void) { return pengyos_fixture() - 7; }\n"
    )
    (work / "nopie.c").write_text(
        "#include <math.h>\nint main(int c, char **v) { (void)v; return (int)cos((double)c); }\n"
    )
    bindir, libdir = appdir / "usr" / "bin", appdir / "usr" / "lib"
    libdir.mkdir(parents=True, exist_ok=True)
    _cc(["-shared", "-fPIC", "-Wl,-soname,libbundled.so.1",
         "-o", str(libdir / "libbundled.so.1.0"), "lib.c"], work)
    (libdir / "libbundled.so.1").symlink_to("libbundled.so.1.0")
    _cc(["-shared", "-fPIC", "-Wl,-soname,libQt6XcbQpa.so.6",
         "-o", str(libdir / "libQt6XcbQpa.so.6"), "lib.c"], work)
    # Link-time stand-in for the library that is deliberately not shipped.
    _cc(["-shared", "-fPIC", "-Wl,-soname,libpengyos-absent.so.3",
         "-o", "libpengyos-absent.so.3", "lib.c"], work)
    _cc(["-o", str(bindir / "sample"), "main.c", str(libdir / "libbundled.so.1.0")], work)
    _cc(["-o", str(bindir / "needs-missing"), "main.c", "libpengyos-absent.so.3"], work)
    _cc(["-no-pie", "-o", str(bindir / "sample-nopie"), "nopie.c", "-lm"], work)


def build(source: Path, output: Path) -> None:
    elf = _extract_runtime(source)
    _set_update_info(elf, UPDATE_INFO)

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

        _compile_elves(tmp, appdir)

        # Desktop entry, at the payload root as the spec requires.
        (appdir / "sample.desktop").write_text(
            "[Desktop Entry]\n"
            "Version=1.0\n"
            "Type=Application\n"
            "Name=Sample App\n"
            "GenericName=Sample\n"
            "Exec=sample %F\n"
            "Icon=sample\n"
            "Terminal=false\n"
            "Categories=Utility;\n",
            encoding="utf-8",
        )

        # Icon: at the root (spec) and in the theme tree (common practice).
        (appdir / "sample.png").write_bytes(_png_1x1())
        (appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
         / "sample.png").write_bytes(_png_1x1())
        (appdir / ".DirIcon").write_bytes(_png_1x1())

        squashfs = tmp / "payload.squashfs"
        subprocess.run(
            ["mksquashfs", str(appdir), str(squashfs), "-root-owned",
             "-noappend", "-no-xattrs", "-comp", "xz", "-quiet"],
            check=True,
        )

        output.parent.mkdir(parents=True, exist_ok=True)
        # appimagetool's layout: runtime, then the squashfs, nothing after.
        with output.open("wb") as fh:
            fh.write(elf)
            fh.write(squashfs.read_bytes())
        output.chmod(0o644)  # never executable in the repo


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    build(Path(argv[1]), FIXTURE)
    print(f"wrote {FIXTURE} ({FIXTURE.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
