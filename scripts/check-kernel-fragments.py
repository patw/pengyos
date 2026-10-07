#!/usr/bin/env python3
"""Report kernel fragment lines that do not survive into an edition's .config.

Buildroot builds the kernel config as: `make <BR2_LINUX_KERNEL_DEFCONFIG>_defconfig`,
merge each BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES entry in order, `olddefconfig`.
Kconfig silently drops any assignment whose dependencies are unmet (or whose
symbol no longer exists), so a fragment can ask for a driver the image never
gets. 2026-09-29: eight such lines in the Desktop (the DesignWare I2C touchpad
drivers under an unset I2C_DESIGNWARE_CORE, the Logitech receiver and PlayStation
drivers missing multicolor LEDs, a renamed Aquantia symbol, ...).

This script replays that flow in a scratch O= dir against a kernel source tree
and lists every fragment line whose final value differs. A later fragment
deliberately overriding an earlier one (Desktop VMD=y over Core's =m) is not a
drop and is not reported.

Usage:
    scripts/check-kernel-fragments.py --kernel-src DIR [EDITION_DEFCONFIG ...]

EDITION_DEFCONFIG defaults to every buildroot-external/configs/pengyos_*_defconfig
that uses a kernel defconfig. Exit 1 if anything was dropped. Needs `make` and a
host C compiler (kconfig itself is compiled); builds no kernel.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXTERNAL = REPO / "buildroot-external"
ASSIGN = re.compile(r"^(CONFIG_\w+)=(.*)$")
UNSET = re.compile(r"^# (CONFIG_\w+) is not set$")


def defconfig_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and line.startswith("BR2_"):
            key, _, value = line.partition("=")
            values[key] = value.strip('"')
    return values


def fragment_values(paths: list[Path]) -> dict[str, tuple[str, Path]]:
    """Final requested value per symbol after all fragments (later wins)."""
    wanted: dict[str, tuple[str, Path]] = {}
    for path in paths:
        for raw in path.read_text().splitlines():
            line = raw.split("#", 1)[0].strip() if not raw.startswith("# CONFIG_") else raw.strip()
            if m := ASSIGN.match(line):
                wanted[m.group(1)] = (m.group(2).strip(), path)
            elif m := UNSET.match(line):
                wanted[m.group(1)] = ("n", path)
    return wanted


def config_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if m := ASSIGN.match(line):
            values[m.group(1)] = m.group(2)
    return values


def check(kernel_src: Path, defconfig: Path) -> list[str]:
    br = defconfig_values(defconfig)
    base = br.get("BR2_LINUX_KERNEL_DEFCONFIG")
    if not base:
        return []
    fragments = [
        Path(f.replace("$(BR2_EXTERNAL_PENGYOS_PATH)", str(EXTERNAL)))
        for f in br.get("BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES", "").split()
    ]
    env = {k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"}
    with tempfile.TemporaryDirectory(prefix="kfrag-") as out:
        make = ["make", "-s", "-C", str(kernel_src), f"O={out}", "ARCH=x86_64"]
        subprocess.run(make + [f"{base}_defconfig"], check=True, env=env, stdout=subprocess.DEVNULL)
        config = Path(out, ".config")
        subprocess.run(
            ["bash", str(kernel_src / "scripts/kconfig/merge_config.sh"), "-m", "-O", out,
             str(config), *map(str, fragments)],
            check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        subprocess.run(make + ["olddefconfig"], check=True, env=env, stdout=subprocess.DEVNULL)
        got = config_values(config)

    problems = []
    for symbol, (value, frag) in sorted(fragment_values(fragments).items()):
        final = got.get(symbol, "n")
        if final != value:
            problems.append(
                f"{frag.relative_to(EXTERNAL)}: {symbol}={value} -> "
                f"{'unset' if final == 'n' else final}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kernel-src", type=Path, required=True, help="kernel source tree")
    parser.add_argument("defconfigs", nargs="*", type=Path)
    args = parser.parse_args()
    defconfigs = args.defconfigs or sorted((EXTERNAL / "configs").glob("pengyos_*_defconfig"))
    failed = False
    for defconfig in defconfigs:
        problems = check(args.kernel_src, defconfig)
        status = "ok" if not problems else f"{len(problems)} dropped"
        print(f"{defconfig.name}: {status}")
        for p in problems:
            print(f"  {p}")
        failed |= bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
