#!/usr/bin/env python3
"""Keep only the Intel Wi-Fi firmware this image's kernel can load.

linux-firmware ships every firmware API version of every iwlwifi device
(135 files, ~195 MB in the Desktop), but a kernel asks for one device family's
firmware from its highest supported API version downwards and loads the first
it finds. So per family only one file matters: the newest one at or below the
kernel's maximum.

The kernel says what its maximum is: iwlwifi.ko carries a `firmware=` modinfo
entry (MODULE_FIRMWARE) naming the highest-API file of every family it drives.
For each family (the file name before the version, and whether the version is
a plain API number or a core release, `cNNN`), this keeps the newest file at or
below that maximum. A family the module does not name is one this kernel never
asks for; its newest file is kept anyway, so a mistake here costs space, never
a device. Files without a version (`.pnvm`, `.sfi`, ...) are left alone.

    prune-iwlwifi.py TARGET_DIR

Exit 0 with a summary line; exit 1 if the module cannot be read while iwlwifi
firmware is present (the prune would be guessing).
"""
from __future__ import annotations

import gzip
import lzma
import pathlib
import re
import sys

NAME = re.compile(r"^(iwlwifi-.+)-(c?)(\d+)\.ucode$")


def module_text(target: pathlib.Path) -> bytes | None:
    for mods in (target / "lib/modules", target / "usr/lib/modules"):
        for ko in sorted(mods.glob("*/kernel/drivers/net/wireless/intel/iwlwifi/iwlwifi.ko*")):
            raw = ko.read_bytes()
            if ko.suffix == ".xz":
                return lzma.decompress(raw)
            if ko.suffix == ".gz":
                return gzip.decompress(raw)
            if ko.suffix == ".zst":
                return None  # no zstd in the Python stdlib: refuse to guess
            return raw
    return None


def maxima(modinfo: bytes) -> dict[tuple[str, str], int]:
    """(family, scheme) -> the highest version the module names."""
    out: dict[tuple[str, str], int] = {}
    for m in re.finditer(rb"firmware=(iwlwifi-[^\x00]+?\.ucode)\x00", modinfo):
        n = NAME.match(m.group(1).decode())
        if n:
            key = (n.group(1), n.group(2))
            out[key] = max(out.get(key, 0), int(n.group(3)))
    return out


def main() -> int:
    target = pathlib.Path(sys.argv[1])
    fw = target / "usr/lib/firmware/intel/iwlwifi"
    if not fw.is_dir():
        fw = target / "lib/firmware/intel/iwlwifi"
    if not fw.is_dir():
        print("prune-iwlwifi: no iwlwifi firmware in the image")
        return 0
    text = module_text(target)
    if text is None:
        print("prune-iwlwifi: iwlwifi firmware present but iwlwifi.ko unreadable", file=sys.stderr)
        return 1
    top = maxima(text)
    if not top:
        # The module declares no firmware names: either its device configs are
        # not built in (Core and Server ship iwlwifi without IWLMVM, so this
        # module drives nothing modern) or the names are built at runtime. The
        # prune only ever saves space, so skipping it costs space, never a
        # device — say so and carry on instead of failing the edition's build.
        print("prune-iwlwifi: iwlwifi.ko names no firmware — keeping all firmware")
        return 0

    groups: dict[tuple[str, str], list[tuple[int, pathlib.Path]]] = {}
    for f in fw.iterdir():
        n = NAME.match(f.name)
        if n and (f.is_file() or f.is_symlink()):
            groups.setdefault((n.group(1), n.group(2)), []).append((int(n.group(3)), f))

    removed, freed = 0, 0
    for key, files in groups.items():
        files.sort()
        limit = top.get(key)
        loadable = [v for v, _ in files if limit is None or v <= limit]
        keep = max(loadable) if loadable else max(v for v, _ in files)
        for v, f in files:
            if v != keep:
                freed += f.lstat().st_size
                f.unlink()
                removed += 1
    # Links (linux-firmware ships a few) must still resolve.
    for f in fw.iterdir():
        if f.is_symlink() and not f.exists():
            print(f"prune-iwlwifi: {f.name} now points at a removed file", file=sys.stderr)
            return 1
    print(f"prune-iwlwifi: kept {len(groups)} firmware files for {len(top)} families the kernel "
          f"names; removed {removed} older ones ({freed // 1048576} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
