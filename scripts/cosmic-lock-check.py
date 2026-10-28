#!/usr/bin/env python3
"""Compare a COSMIC component's Cargo.lock with a reference, package by package.

Why: build-cosmic.sh compiles ~2,500 crates, and every crate's build script and
proc-macros run on the build host. The lock decides which crates those are, and it
was not binding: the build ran without `--locked`, and when the libcosmic coherence
pin did not take it ran `cargo generate-lockfile`, which re-resolves the whole graph
to whatever crates.io serves that day. Measured 2026-10-28: cosmic-idle's lock
carried 21 crates and 165 versions its upstream lock does not, which nobody had
looked at. Upstream's lock plus the pin is not reproducible either: the pin pulls in
libcosmic's own git dependencies by tag, and a crate new to the graph resolves to the
newest version that day.

So PengyOS owns the resolution: scripts/cosmic-locks/<component>.Cargo.lock is the
lock each component is built with (`--locked`), and a change to it is a reviewed
commit. This tool is the review: `build-cosmic.sh --relock` runs it to show what a
re-resolve changed, against the committed lock (or upstream's, the first time).

Usage:
  cosmic-lock-check.py <lock> <reference-lock>
  cosmic-lock-check.py <lock> git:<component-dir>      upstream's lock at its HEAD
exit 0 when the two hold the same packages (name, version, source, checksum);
1 with every difference listed otherwise.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import tomllib


def packages(text: str) -> dict[tuple[str, str, str], str]:
    """(name, version, source) -> checksum for every non-path package in a lock."""
    out = {}
    for p in tomllib.loads(text).get("package", []):
        if "source" in p:   # a path dependency is the component's own code
            out[(p["name"], p.get("version", ""), p["source"])] = p.get("checksum", "")
    return out


def differences(ref: str, got: str) -> list[str]:
    a, b = packages(ref), packages(got)
    out = []
    for key in sorted(set(a) | set(b)):
        name, version, source = key
        if key not in b:
            out.append(f"- {name} {version} ({source})")
        elif key not in a:
            out.append(f"+ {name} {version} ({source})")
        elif a[key] != b[key]:
            out.append(f"! {name} {version}: checksum {b[key][:16]} is not the "
                       f"reference's {a[key][:16]}")
    return out


def read_reference(spec: str) -> str | None:
    if not spec.startswith("git:"):
        return Path(spec).read_text()
    r = subprocess.run(["git", "-C", spec[4:], "show", "HEAD:Cargo.lock"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("Usage:")[1], file=sys.stderr)
        return 2
    lock, spec = Path(argv[0]), argv[1]
    ref = read_reference(spec)
    if ref is None:
        print(f"{lock}: no reference lock at {spec} (upstream ships none); every "
              f"package is new - review the whole lock", file=sys.stderr)
        return 1
    diff = differences(ref, lock.read_text())
    if not diff:
        print(f"{lock}: same packages as {spec}")
        return 0
    added = sum(1 for d in diff if d.startswith("+"))
    removed = sum(1 for d in diff if d.startswith("-"))
    print(f"{lock}: {added} added, {removed} removed, {len(diff) - added - removed} "
          f"checksum change(s) against {spec}:", file=sys.stderr)
    for line in diff:
        print(f"  {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
