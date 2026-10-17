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
    scripts/check-kernel-fragments.py --built KERNEL_CONFIG --br-config BR2_CONFIG

EDITION_DEFCONFIG defaults to every buildroot-external/configs/pengyos_*_defconfig
that uses a kernel defconfig. Exit 1 if anything was dropped. Needs `make` and a
host C compiler (kconfig itself is compiled); builds no kernel.

--built checks a kernel .config Buildroot has already resolved, and is what the
core post-build runs in every edition, so a drop fails the build. It checks every
fragment line plus each fragment's sibling kernel-required.txt
(board/<layer>/kernel-required.txt). That list holds options we depend on but
never set, because the base defconfig happens to provide them, so a kernel bump
can take them away without any fragment changing (2026-10-17: PCIe hotplug and
vga_switcheroo had never been on, and the dGPU of a hybrid laptop never powered
down). Each line is `CONFIG_FOO` (y or m) or `CONFIG_FOO=y`, then `# why`.
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


def required_values(path: Path) -> dict[str, tuple[str, str]]:
    """kernel-required.txt: symbol -> (wanted value, or "ym" for either; reason)."""
    wanted = {}
    for raw in path.read_text().splitlines():
        line, _, why = raw.partition("#")
        line = line.strip()
        if not line:
            continue
        symbol, _, value = line.partition("=")
        if not re.fullmatch(r"CONFIG_\w+", symbol) or value not in ("", "y", "m"):
            raise SystemExit(f"{path}: bad line: {raw!r}")
        wanted[symbol] = (value or "ym", why.strip())
    return wanted


def fragment_paths(br: dict[str, str]) -> list[Path]:
    return [
        Path(f.replace("$(BR2_EXTERNAL_PENGYOS_PATH)", str(EXTERNAL)))
        for f in br.get("BR2_LINUX_KERNEL_CONFIG_FRAGMENT_FILES", "").split()
    ]


def check_built(kernel_config: Path, br_config: Path) -> list[str]:
    fragments = fragment_paths(defconfig_values(br_config))
    got = config_values(kernel_config)
    problems = []
    for symbol, (value, frag) in sorted(fragment_values(fragments).items()):
        final = got.get(symbol, "n")
        if final != value:
            problems.append(
                f"{frag.relative_to(EXTERNAL)}: {symbol}={value} -> "
                f"{'unset' if final == 'n' else final}"
            )
    for frag in fragments:
        req = frag.with_name("kernel-required.txt")
        if not req.is_file():
            continue
        for symbol, (value, why) in sorted(required_values(req).items()):
            final = got.get(symbol, "n")
            if final not in (("y", "m") if value == "ym" else (value,)):
                problems.append(
                    f"{req.relative_to(EXTERNAL)}: {symbol} is "
                    f"{'unset' if final == 'n' else final} ({why})"
                )
    return problems


def check(kernel_src: Path, defconfig: Path) -> list[str]:
    br = defconfig_values(defconfig)
    base = br.get("BR2_LINUX_KERNEL_DEFCONFIG")
    if not base:
        return []
    fragments = fragment_paths(br)
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
    parser.add_argument("--kernel-src", type=Path, help="kernel source tree")
    parser.add_argument("--built", type=Path, help="a kernel .config Buildroot resolved")
    parser.add_argument("--br-config", type=Path, help="that build's Buildroot .config")
    parser.add_argument("defconfigs", nargs="*", type=Path)
    args = parser.parse_args()
    if args.built:
        if not args.br_config:
            parser.error("--built needs --br-config")
        problems = check_built(args.built, args.br_config)
        for p in problems:
            print(f"kernel config: {p}", file=sys.stderr)
        return 1 if problems else 0
    if not args.kernel_src:
        parser.error("--kernel-src or --built is required")
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
