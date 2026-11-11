#!/usr/bin/env python3
"""Write the EDID of PengyOS Desktop's virtual display (1920x1080 at 60 Hz).

    scripts/virtual-display-edid.py [OUT]   default: the Desktop overlay's copy

pengyos-virtual-display hands it to the kernel (DRM debugfs edid_override) for
the HDMI port it forces on when a Desktop boots with no monitor, so the
compositor gets one 1080p output (see that script). The binary in the overlay
is this script's output; scripts/tests/test_virtual_display.py checks they
match, so change the timing here and rerun it.

128 bytes, EDID 1.4, no extension block: a digital 8-bit RGB monitor named
"PengyOS VDisp" whose only timing is the CEA-861 1080p60 one (148.5 MHz) that
every HDMI encoder drives.
"""
from __future__ import annotations

import pathlib
import struct
import sys

OUT = (pathlib.Path(__file__).resolve().parents[1] / "buildroot-external" / "board"
       / "desktop" / "rootfs-overlay" / "usr" / "share" / "pengyos" / "virtual-display.edid")


def timing(clock_khz, ha, hb, hfp, hsw, va, vb, vfp, vsw, wmm, hmm) -> bytes:
    """An 18-byte detailed timing descriptor (EDID 1.4, section 3.10.2)."""
    c = clock_khz // 10
    return bytes([
        c & 0xFF, c >> 8,
        ha & 0xFF, hb & 0xFF, (ha >> 8) << 4 | hb >> 8,
        va & 0xFF, vb & 0xFF, (va >> 8) << 4 | vb >> 8,
        hfp & 0xFF, hsw & 0xFF, (vfp & 0xF) << 4 | vsw & 0xF,
        (hfp >> 8) << 6 | (hsw >> 8) << 4 | (vfp >> 4) << 2 | vsw >> 4,
        wmm & 0xFF, hmm & 0xFF, (wmm >> 8) << 4 | hmm >> 8,
        0, 0, 0x1E,  # no border; digital separate sync, +hsync +vsync
    ])


def text(tag: int, s: str) -> bytes:
    """A display descriptor holding up to 13 characters, newline then spaces."""
    b = s.encode("ascii")[:13]
    if len(b) < 13:
        b += b"\n" + b" " * (12 - len(b))
    return bytes([0, 0, 0, tag, 0]) + b


def edid() -> bytes:
    b = bytearray(b"\x00\xff\xff\xff\xff\xff\xff\x00")
    b += struct.pack(">H", (ord("P") - 64) << 10 | (ord("N") - 64) << 5 | (ord("G") - 64))
    b += struct.pack("<HI", 1, 0)             # product 1, no serial
    b += bytes([1, 2026 - 1990])              # week 1 of 2026
    b += bytes([1, 4])                        # EDID 1.4
    b += bytes([0xA0, 53, 30, 120, 0x06])     # digital 8 bpc, 53x30 cm, gamma 2.2,
                                              # RGB, sRGB, first timing is native
    b += bytes([0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54])  # sRGB
    b += bytes(3)                             # no established timings
    b += b"\x01\x01" * 8                      # no standard timings
    b += timing(148500, 1920, 280, 88, 44, 1080, 45, 4, 5, 531, 299)
    b += bytes([0, 0, 0, 0xFD, 0, 56, 76, 30, 81, 15, 0, 0x0A]) + b" " * 6  # range
    b += text(0xFC, "PengyOS VDisp")          # monitor name
    b += text(0xFE, "virtual")                # unspecified text
    b += b"\x00"                              # no extension blocks
    b += bytes([-sum(b) & 0xFF])
    assert len(b) == 128 and sum(b) % 256 == 0
    return bytes(b)


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    out.write_bytes(edid())
    print(f"wrote {out} (128 bytes)")
