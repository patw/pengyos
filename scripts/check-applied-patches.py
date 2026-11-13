#!/usr/bin/env python3
"""Refuse output trees whose packages were patched from somewhere other than the repo.

Buildroot records every patch it applied, by full path, in
build/<pkg>-<ver>/.applied_patches_list, and `legal-info` (run by every release
for the SBOM) copies those files from that path again. A test build from a
branch worktree in a real output tree records the worktree's paths. Once the
worktree is removed, the release dies at legal-info, hours into the build
(26.11.15, 2026-11-13: host-go-src and podman, from /data/tmp/secfix). While the
worktree still exists, the release ships the branch's patches instead of main's
without any error.

A recorded patch is fine when it still exists and, if it is one of ours (under a
buildroot-external/), it is under this repo's buildroot-external/. Anything else
names the build dir to dirclean.

    check-applied-patches.py --repo /data/PengyOS OUTPUT_DIR...

Exit 0 when every tree is clean, 1 with a list otherwise.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def problems(output: Path, repo: Path) -> list[tuple[str, str, str]]:
    """(build dir name, patch path, reason) for every stale entry in one output tree."""
    ours = str(repo.resolve() / "buildroot-external") + "/"
    out = []
    for lst in sorted(output.glob("build/*/.applied_patches_list")):
        for line in lst.read_text(errors="replace").splitlines():
            p = line.strip()
            if not p:
                continue
            if "/buildroot-external/" in p and not p.startswith(ours):
                out.append((lst.parent.name, p, "not from this repo"))
            elif not Path(p).exists():
                out.append((lst.parent.name, p, "no longer exists"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("outputs", type=Path, nargs="+")
    a = ap.parse_args(argv)
    bad = 0
    for output in a.outputs:
        if not (output / "build").is_dir():
            continue
        found = problems(output, a.repo)
        seen = set()
        for build_dir, patch, why in found:
            print(f"{output}: build/{build_dir}: {patch} ({why})")
            seen.add(build_dir)
        if seen:
            bad += len(seen)
            print(f"  fix: make -C {output} <pkg>-dirclean, for the package of each of: "
                  + ", ".join(sorted(seen)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
