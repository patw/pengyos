#!/usr/bin/env python3
"""Force one libcosmic commit across the COSMIC component set.

Why: libcosmic carries no `epoch-*` tags and every COSMIC component declares it as
an unversioned git dependency, so each component's Cargo.lock froze its own
libcosmic commit. Measured 2026-10-05: the Desktop image built COSMIC against 14
distinct libcosmic commits (2025-10-13 .. 2026-09-21). That is the real mechanism
behind the failure PengyOS hit once already - a 1.9 greeter on a 1.8 compositor,
no wallpaper. See docs/cosmic-epoch-1.9.md.

How: append/merge a `[patch."https://github.com/pop-os/libcosmic"]` entry per
crate the component actually resolves out of that source, all naming one rev. The
rev lives in scripts/cosmic-libcosmic.pin.

Two things this has to survive, both learned the hard way on the first run:

1. **A cargo patch must point at a different source** than the dependency it
   replaces - patching `https://github.com/pop-os/libcosmic` with the same URL is
   rejected outright. pop-os' own idiom is a trailing `//` in the URL, used in
   cosmic-greeter's commented-out libcosmic patch block and its live
   cosmic-protocols patches. We use it.
2. **Nine components already declare a libcosmic patch section**, usually spelled
   with single quotes (`[patch.'https://github.com/pop-os/libcosmic']` on
   cosmic-osd) and usually containing only commented-out examples. TOML treats
   single-quoted and basic strings as the same key, so appending a second section
   is a hard parse error: "duplicate key `https://github.com/pop-os/libcosmic`".
   So entries are merged into the existing section when there is one, and any
   active entry for a crate we pin is commented out rather than left alongside
   ours (duplicate keys in one table are an error).

Idempotent: every line this tool adds carries SENTINEL, and a re-run strips them
first. build-cosmic.sh also drops local changes before re-checking-out.

Usage:
  cosmic-libcosmic-pin.py <component-dir> <rev>           apply the coherence pin
  cosmic-libcosmic-pin.py --verify <component-dir> <rev>  report the revs its lock resolves to
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

SENTINEL = "# pengyos-libcosmic-coherence"
SOURCE = "https://github.com/pop-os/libcosmic"
# `[patch."url"]` or `[patch.'url']` - TOML considers these the same key.
SECTION = re.compile(r"^\[patch\.[\"']" + re.escape(SOURCE) + r"[\"']\]\s*$")
ANY_TABLE = re.compile(r"^\[")
ENTRY = re.compile(r"^([A-Za-z0-9_-]+)\s*=")
LOCK_SOURCE = re.compile(
    r'\[\[package\]\]\nname = "([^"]+)"(?:\nversion = "[^"]+")?\n'
    r'source = "git\+https://github\.com/pop-os/libcosmic(?:\.git|/)?[^"]*"'
)


def crates_from_lock(component: Path) -> list[str]:
    """Crate names this component resolves out of pop-os/libcosmic."""
    lock = component / "Cargo.lock"
    if not lock.is_file():
        return []
    return sorted({m.group(1) for m in LOCK_SOURCE.finditer(lock.read_text())})


def strip_ours(lines: list[str]) -> list[str]:
    return [l for l in lines if SENTINEL not in l]


def find_section(lines: list[str]) -> "tuple[int, int] | None":
    """(header_index, section_end) for an existing libcosmic patch section."""
    for i, line in enumerate(lines):
        if SECTION.match(line):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if ANY_TABLE.match(lines[j]) and not lines[j].lstrip().startswith("#"):
                    end = j
                    break
            return i, end
    return None


def lock_revs(component: Path) -> set[str]:
    """Every libcosmic commit this component's Cargo.lock actually resolves to.

    Deliberately looser than LOCK_SOURCE: it matches the source string anywhere in
    the file, so a lock that spells the source differently (a `?rev=` patch entry,
    a `?branch=` pin, a different field order) still counts. A verification step
    that can be fooled by formatting is worse than no verification step.
    """
    lock = component / "Cargo.lock"
    if not lock.is_file():
        return set()
    revs = set()
    for m in re.finditer(r'source = "git\+https://github\.com/pop-os/libcosmic[^"]*"',
                         lock.read_text()):
        revs.update(re.findall(r"[0-9a-f]{40}", m.group(0)))
    return revs


def verify(component: Path, rev: str) -> int:
    revs = lock_revs(component)
    if not revs:
        print(f"[cosmic] {component.name}: no libcosmic dependency")
        return 0
    # Compare by prefix so a short pin still matches a full lock sha.
    if all(r.startswith(rev) for r in revs):
        print(f"[cosmic] {component.name}: coherent ({rev[:12]})")
        return 0
    print(f"[cosmic] {component.name}: INCOHERENT - lock resolves to "
          f"{sorted(r[:12] for r in revs)}, pin is {rev[:12]}")
    return 1


def main(argv: list[str]) -> int:
    if argv[1:2] == ["--verify"]:
        if len(argv) != 4:
            sys.stderr.write("usage: cosmic-libcosmic-pin.py --verify <component-dir> <rev>\n")
            return 2
        if not re.fullmatch(r"[0-9a-f]{7,40}", argv[3]):
            sys.stderr.write(f"cosmic-libcosmic-pin: rev is not a commit sha: {argv[3]!r}\n")
            return 2
        return verify(Path(argv[2]), argv[3])
    if len(argv) != 3:
        sys.stderr.write("usage: cosmic-libcosmic-pin.py <component-dir> <rev>\n")
        return 2
    component, rev = Path(argv[1]), argv[2]
    toml = component / "Cargo.toml"
    if not toml.is_file():
        return 0                      # not a cargo component (icons, themes, …)
    if not re.fullmatch(r"[0-9a-f]{7,40}", rev):
        sys.stderr.write(f"cosmic-libcosmic-pin: rev is not a commit sha: {rev!r}\n")
        return 2

    crates = crates_from_lock(component)
    if not crates:
        # No Cargo.lock (six components ship none upstream): cargo resolves at
        # build time, so the patch has to be in place before it does. Patch the
        # crate every one of them depends on; the rest resolve against it.
        crates = ["libcosmic"]

    lines = strip_ours(toml.read_text().splitlines())
    entries = [f'{c} = {{ git = "{SOURCE}//", rev = "{rev}" }} {SENTINEL}' for c in crates]

    found = find_section(lines)
    if found is not None:
        head, end = found
        # Comment out any active entry for a crate we are pinning: two keys of the
        # same name in one table is a TOML error, and ours must win.
        want = set(crates)
        for k in range(head + 1, end):
            m = ENTRY.match(lines[k])
            if m and m.group(1) in want and not lines[k].lstrip().startswith("#"):
                lines[k] = f"# {lines[k].rstrip()}  # replaced by the pengyos coherence pin"
        lines[head + 1:head + 1] = entries
    else:
        lines += ["", f"[patch.\"{SOURCE}\"] {SENTINEL}"] + entries

    toml.write_text("\n".join(lines).rstrip() + "\n")
    where = "merged into the existing patch section" if found else "new patch section"
    print(f"[cosmic] {component.name}: libcosmic pinned to {rev[:12]} "
          f"({len(crates)} crate{'s' if len(crates) != 1 else ''}, {where})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
