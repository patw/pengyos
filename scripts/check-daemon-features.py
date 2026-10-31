#!/usr/bin/env python3
"""Assert the built rootfs still has the daemon capabilities an edition claims.

The kernel has this guard already: `board/<layer>/kernel-required.txt` lists the
options an edition depends on but no fragment sets, and
`scripts/check-kernel-fragments.py --built` fails the build when Kconfig drops
one (2026-10-17: PCIe hotplug had never been on and a hybrid laptop's dGPU never
powered down). The userspace half had no such guard, and it cost exactly the same
kind of bug: Buildroot makes every optional daemon capability an opt-in
sub-option, so an image can ship a daemon that answers every handshake and
implements half its protocols. Until 26.10.4x, bluez5_utils was built
`--disable-hid --disable-hog`: a Bluetooth gamepad paired, bonded, resolved its
GATT HID service, reported a battery level, and never became an input device.
Nothing in the build said a word.

This script checks a features file (default:
`buildroot-external/board/desktop/features-required.txt`) against an assembled
rootfs and exits 1 listing every claim that no longer holds. The desktop
post-build runs it, so the failure lands on the build host instead of on a
machine whose controller does not work.

Usage:
    scripts/check-daemon-features.py --target-dir DIR [--require FILE ...]

Features file format:

    kind  argument...   [# why]

    contains <path> <needle>  file exists under / and contains the literal text
                              (binary-safe: searches the file as bytes)
    exec     <path>           file exists under / and is executable
    exec-any <basename>       an executable of that name sits in usr/bin,
                              usr/sbin, usr/libexec/*, bin or sbin
    exists   <path>           file exists under / (data files: udev rules, ...)

`# why` is carried into the failure message; make it name the Buildroot symbol
so the fix is one line away. Blank lines and `#` comment lines are ignored.

Exit: 0 = every claim holds, 1 = at least one does not, 2 = usage problem.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_REQUIRE = REPO / "buildroot-external" / "board" / "desktop" / "features-required.txt"

# Where exec-any looks. Deliberately shallow: a name is a claim about the image
# as a user meets it (a shell on $PATH), not about some helper in a private dir.
EXEC_DIRS = ("bin", "sbin", "usr/bin", "usr/sbin")


def wanted_exec_dirs(target: Path) -> list[Path]:
    dirs = [target / d for d in EXEC_DIRS]
    libexec = target / "usr" / "libexec"
    if libexec.is_dir():
        dirs.extend(sorted(p for p in libexec.iterdir() if p.is_dir()))
    dirs.append(libexec)
    return dirs


def in_target(target: Path, path: Path) -> Path:
    """Follow PATH's symlinks the way the booted image would.

    Packages often install absolute links (ksmbd-tools: usr/bin/ksmbd.mountd ->
    /usr/libexec/ksmbd.tools), which on the build host would point outside the
    rootfs. Re-root those under TARGET; relative links resolve as they are.
    """
    for _ in range(40):
        if not path.is_symlink():
            return path
        link = Path(os.readlink(path))
        path = target / str(link).lstrip("/") if link.is_absolute() else path.parent / link
    return path


def parse_features(path: Path) -> list[tuple[int, str, list[str], str]]:
    checks = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        reason = ""
        if "  #" in line:
            line, _, reason = line.partition("  #")
            reason = reason.strip()
        parts = line.split()
        kind, args = parts[0], parts[1:]
        checks.append((lineno, kind, args, reason))
    return checks


def run_check(target: Path, kind: str, args: list[str]) -> str | None:
    """Return None when the claim holds, else a short problem description."""
    if kind == "contains":
        if len(args) != 2:
            return "contains needs <path> <needle>"
        rel, needle = args
        path = in_target(target, target / rel.lstrip("/"))
        if not path.is_file():
            return f"{rel}: missing"
        if needle.encode() not in path.read_bytes():
            return f"{rel}: no {needle!r} in it"
        return None
    if kind == "exec":
        if len(args) != 1:
            return "exec needs <path>"
        rel = args[0]
        path = in_target(target, target / rel.lstrip("/"))
        if not path.is_file():
            return f"{rel}: missing"
        if not os.access(path, os.X_OK):
            return f"{rel}: not executable"
        return None
    if kind == "exec-any":
        if len(args) != 1:
            return "exec-any needs <basename>"
        name = args[0]
        for d in wanted_exec_dirs(target):
            candidate = in_target(target, d / name)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return None
        return f"{name}: no executable by that name in bin, sbin, usr/bin, usr/sbin, usr/libexec/*"
    if kind == "exists":
        if len(args) != 1:
            return "exists needs <path>"
        rel = args[0]
        if not in_target(target, target / rel.lstrip("/")).exists():
            return f"{rel}: missing"
        return None
    return f"unknown check {kind!r}"


def check(target: Path, requires: list[Path]) -> list[str]:
    problems = []
    for req in requires:
        for lineno, kind, args, reason in parse_features(req):
            problem = run_check(target, kind, args)
            if problem:
                why = f"  # {reason}" if reason else ""
                problems.append(f"{req.name}:{lineno}: {problem}{why}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--target-dir", type=Path, required=True,
                        help="the assembled rootfs (TARGET_DIR)")
    parser.add_argument("--require", type=Path, action="append",
                        help=f"features file (default: {DEFAULT_REQUIRE})")
    args = parser.parse_args()
    if not args.target_dir.is_dir():
        parser.error(f"--target-dir {args.target_dir} is not a directory")
    requires = args.require or [DEFAULT_REQUIRE]
    for req in requires:
        if not req.is_file():
            parser.error(f"no such features file: {req}")
    problems = check(args.target_dir, requires)
    for p in problems:
        print(f"daemon features: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
