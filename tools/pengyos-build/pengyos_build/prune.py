"""Remove the build dirs of package versions a target no longer builds.

Buildroot builds every package in ``<out>/build/<pkg>-<version>/`` and never
removes the old directory when a version moves (pengy 1.15.5 -> 1.16.1, a
Buildroot bump), so each output tree keeps every version it ever built. They
are dead weight: nothing reads them, and removing one rebuilds nothing.

What is current comes from ``<out>/images/<target>.show-info.json``, which the
SBOM step of every build writes from ``make show-info``. A directory is pruned
only when all of these hold, and anything this cannot place is left alone:

* it is a real directory (not a symlink) directly under ``build/`` holding
  Buildroot ``.stamp_*`` files, and it is not any package's current build dir;
* its name is ``<pkg>-<version>`` for a package show-info lists (the rest after
  ``<pkg>-`` starts with a digit, or ``v`` and a digit, so ``util-linux-libs-*``
  is never read as an old ``util-linux``);
* that package's current build dir exists on disk (show-info matches the tree);
* neither it nor its stamps changed after show-info.json was written. A version
  bump changes ``.mk`` files, not ``.config``, so a show-info older than the
  bump calls the new version old; this rule keeps the new one.

Not pruned: packages that are no longer enabled at all, and the toolchain
(glibc, gcc), which show-info does not list. Those are rare and best left to a
person (``make <pkg>-dirclean``).

    pengyos-build prune <target> [--apply]
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .acquire import default_build_root
from .configure import output_dir
from .targets import Target

_VERSION = re.compile(r"v?\d")


class PruneError(RuntimeError):
    """Nothing can be pruned safely (no show-info, unreadable show-info)."""


@dataclass(frozen=True)
class Old:
    """One build dir of a version the target no longer builds."""

    path: Path
    package: str
    current: str
    size: int  # bytes on disk, hard links counted once


@dataclass
class PruneResult:
    target: str
    output: Path
    old: list[Old] = field(default_factory=list)
    applied: bool = False

    @property
    def size(self) -> int:
        return sum(o.size for o in self.old)

    def __str__(self) -> str:
        verb = "removed" if self.applied else "would remove"
        lines = [f"{self.target}: {verb} {len(self.old)} old build dir(s), "
                 f"{self.size / 2**30:.1f} GiB"]
        for o in self.old:
            lines.append(f"  {o.path.name} (current {o.current}, "
                         f"{o.size / 2**20:.0f} MiB)")
        if self.old and not self.applied:
            lines.append("  (dry run: --apply removes them)")
        return "\n".join(lines)


def show_info_path(output: Path, target: Target) -> Path:
    return output / "images" / f"{target.name}.show-info.json"


def current_dirs(show_info: dict) -> dict[str, str]:
    """Package name -> its current directory name under build/."""
    cur = {}
    for name, info in show_info.items():
        bd = info.get("build_dir") if isinstance(info, dict) else None
        if not bd:
            continue
        parts = Path(bd).parts
        if len(parts) >= 2 and parts[0] == "build":
            cur[name] = parts[1]
    return cur


def _newest(path: Path) -> float:
    times = [path.stat().st_mtime]
    for q in path.iterdir():
        if q.name.startswith(".stamp_"):
            times.append(q.lstat().st_mtime)
    return max(times)


def _has_stamps(path: Path) -> bool:
    return any(q.name.startswith(".stamp_") for q in path.iterdir())


def _size(path: Path) -> int:
    seen, total = set(), 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            st = os.lstat(os.path.join(root, f))
            if (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                total += st.st_blocks * 512
    return total


def find_old(output: Path, show_info_file: Path) -> list[Old]:
    try:
        data = json.loads(show_info_file.read_text())
    except FileNotFoundError:
        raise PruneError(f"no {show_info_file}: build the target first "
                         "(its SBOM step writes it)") from None
    except (OSError, ValueError) as exc:
        raise PruneError(f"cannot read {show_info_file}: {exc}") from None
    if not isinstance(data, dict):
        raise PruneError(f"{show_info_file} is not a show-info object")
    cur = current_dirs(data)
    if not cur:
        raise PruneError(f"{show_info_file} names no build dirs")
    build = output / "build"
    if not build.is_dir():
        return []
    written = show_info_file.stat().st_mtime
    keep = set(cur.values())
    # Longest package name first, so "host-qt6base" is tried before "host-qt6".
    names = sorted(cur, key=len, reverse=True)
    old = []
    for p in sorted(build.iterdir()):
        if p.is_symlink() or not p.is_dir() or p.name in keep or not _has_stamps(p):
            continue
        for n in names:
            if not p.name.startswith(n + "-"):
                continue
            if not _VERSION.match(p.name[len(n) + 1:]):
                continue
            if not (build / cur[n]).is_dir() or _newest(p) >= written:
                break
            old.append(Old(path=p, package=n, current=cur[n], size=_size(p)))
            break
    return old


def prune_target(
    target: Target,
    build_root: Path | str | None = None,
    *,
    apply: bool = False,
    env: dict[str, str] | None = None,
) -> PruneResult:
    root = Path(build_root) if build_root is not None else default_build_root(env)
    output = output_dir(root, target)
    if not output.is_dir():
        raise PruneError(f"target '{target.name}' has no output dir at {output}")
    result = PruneResult(target=target.name, output=output,
                         old=find_old(output, show_info_path(output, target)))
    if apply:
        for o in result.old:
            shutil.rmtree(o.path)
        result.applied = True
    return result
