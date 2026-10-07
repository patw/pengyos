#!/usr/bin/env python3
"""Fail the build if any program or library in the image needs a shared
library the image doesn't have.

    check-needed-libs.py TARGET_DIR

Run at the end of each edition's post-build. 26.10.3's Desktop shipped a
cosmic-comp that needed libinput.so.10, which no package provided any more
(it had come in with Weston); the compositor exited 127 at every start and the
QEMU check, which waits for a login prompt, passed. This reads each ELF's
DT_NEEDED and DT_RUNPATH/DT_RPATH (a small parser, no host tools) and looks for
every needed library in its run path and the system directories, inside
TARGET_DIR.
"""
import os
import struct
import sys

SYSTEM_DIRS = ["/usr/lib", "/lib", "/usr/lib64", "/lib64"]
# Searched for everything: the compat libs (cosmic-store's wrapper puts them on
# LD_LIBRARY_PATH) and systemd's private library directory.
EXTRA_DIRS = ["/usr/lib/compat", "/usr/lib/systemd"]
SKIP_PREFIXES = ("usr/lib/modules/", "usr/lib/firmware/", "lib/modules/", "lib/firmware/")

PT_LOAD, PT_DYNAMIC = 1, 2
DT_NEEDED, DT_STRTAB, DT_RPATH, DT_RUNPATH = 1, 5, 15, 29


def dynamic_info(path):
    """(needed, runpath) for a 64-bit little-endian ELF, or None if it has no
    dynamic section (static, relocatable, not ELF, or another class)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    loads, dyn = [], None
    for i in range(phnum):
        off = phoff + i * phentsize
        if off + 56 > len(data):
            return None
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from("<IIQQQQ", data, off)
        if p_type == PT_LOAD:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == PT_DYNAMIC:
            dyn = (p_offset, p_filesz)
    if dyn is None:
        return None

    def vaddr_to_off(addr):
        for vaddr, offset, size in loads:
            if vaddr <= addr < vaddr + size:
                return offset + addr - vaddr
        return None

    entries = []
    for off in range(dyn[0], min(dyn[0] + dyn[1], len(data) - 15), 16):
        tag, val = struct.unpack_from("<qQ", data, off)
        if tag == 0:
            break
        entries.append((tag, val))
    strtab = next((vaddr_to_off(v) for t, v in entries if t == DT_STRTAB), None)
    if strtab is None:
        return None

    def string(off):
        end = data.find(b"\0", strtab + off)
        return data[strtab + off:end].decode("utf-8", "replace")

    needed = [string(v) for t, v in entries if t == DT_NEEDED]
    runpath = []
    for t, v in entries:
        if t in (DT_RUNPATH, DT_RPATH):
            runpath += [p for p in string(v).split(":") if p]
    return needed, runpath


def main(target):
    target = os.path.abspath(target)
    missing = []
    for top in ("usr/bin", "usr/libexec", "usr/lib", "opt"):
        root = os.path.join(target, top)
        for dirpath, _dirs, files in os.walk(root):
            rel_dir = os.path.relpath(dirpath, target)
            if rel_dir.startswith(SKIP_PREFIXES):
                continue
            for name in files:
                path = os.path.join(dirpath, name)
                if os.path.islink(path):
                    continue
                info = dynamic_info(path)
                if not info:
                    continue
                needed, runpath = info
                origin = "/" + rel_dir
                dirs = [p.replace("$ORIGIN", origin).replace("${ORIGIN}", origin) for p in runpath]
                dirs += SYSTEM_DIRS + EXTRA_DIRS
                for lib in needed:
                    if not any(os.path.lexists(os.path.join(target, d.lstrip("/"), lib)) for d in dirs):
                        missing.append(f"/{os.path.relpath(path, target)}: {lib}")
    if missing:
        print("check-needed-libs: libraries the image needs but doesn't contain:", file=sys.stderr)
        for m in sorted(set(missing)):
            print("  " + m, file=sys.stderr)
        return 1
    print("check-needed-libs: every needed shared library is in the image")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
