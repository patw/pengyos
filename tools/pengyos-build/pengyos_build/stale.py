"""Find already-built packages that a ``.config`` change left stale.

Buildroot never rebuilds a package because its options changed: re-running
``make <defconfig>`` rewrites ``.config``, but a package whose
``.stamp_built`` exists is skipped. So turning on a sub-option such as
``BR2_PACKAGE_LINUX_FIRMWARE_RTL_RTW88`` in an existing output dir silently
does nothing — the image keeps the firmware set it was first built with
(2026-09-29: rtw88 firmware "enabled" but absent from the Desktop and Core
outputs, both configured before the option reached their defconfig).

:func:`stale_packages` maps every changed ``BR2_PACKAGE_*`` symbol to the
package that owns it — the longest ``BR2_PACKAGE_<X>`` prefix that is itself a
package symbol set to ``y`` — and keeps the ones with a build dir in
``<output>/build``. Removing that build dir is what ``make <pkg>-dirclean``
does; the next build then rebuilds and reinstalls the package.

:func:`dependency_changes` catches the other half: a package that must be
rebuilt because its *dependencies* changed although none of its own options
did — packages enable optional support for whatever else is in the build
(mesa3d when libglvnd appeared; cups and sane-backends when avahi did, which
had left them built without network discovery). ``make show-info`` before and
after the reconfigure gives each package's resolved dependency list; a built
package whose list changed is rebuilt.

What neither catches: files a newly *disabled* package, or a rebuilt package
that installs less than it did, left in ``target/`` (packages turned off are
listed in :attr:`StaleReport.disabled`; :mod:`pengyos_build.leftovers` names
the files after the next build; only a clean build removes them).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

PREFIX = "BR2_PACKAGE_"

_LINE = re.compile(r"^(BR2_[A-Za-z0-9_]+)=(.*)$")


def parse_config(text: str) -> dict[str, str]:
    """``.config`` text -> ``{symbol: value}`` for the symbols that are set."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if m:
            values[m.group(1)] = m.group(2)
    return values


def changed_symbols(old: dict[str, str], new: dict[str, str]) -> set[str]:
    """Symbols whose value differs (set, unset, or changed) between two configs."""
    return {s for s in old.keys() | new.keys() if old.get(s) != new.get(s)}


def _normalize(name: str) -> str:
    return name.upper().replace("-", "_")


def owning_package(symbol: str, packages: set[str]) -> str | None:
    """The package symbol stem (``LINUX_FIRMWARE``) owning ``symbol``, if any.

    ``packages`` holds the stems of *built* packages; the longest stem that is
    ``symbol``'s own stem or a ``_``-bounded prefix of it wins. (A ``y``
    sub-option such as ``LINUX_FIRMWARE_RTL_RTW88`` looks just like a package
    symbol in ``.config``; having a build dir is what makes a stem a package.)
    """
    if not symbol.startswith(PREFIX):
        return None
    tokens = symbol[len(PREFIX):].split("_")
    for n in range(len(tokens), 0, -1):
        stem = "_".join(tokens[:n])
        if stem in packages:
            return stem
    return None


def _build_dirs(build: Path) -> dict[str, Path]:
    """Normalized build-dir name (``LINUX_FIRMWARE_20260810``) -> path."""
    if not build.is_dir():
        return {}
    return {_normalize(p.name): p for p in build.iterdir() if p.is_dir()}


def _dir_for(stem: str, dirs: dict[str, Path]) -> Path | None:
    """The build dir of package ``stem``: ``<stem>_<version>``, version first char a digit/v."""
    for name, path in dirs.items():
        rest = name[len(stem) + 1:] if name.startswith(stem + "_") else ""
        if rest and (rest[0].isdigit() or (rest[0] == "V" and rest[1:2].isdigit())):
            return path
    return None


@dataclass(frozen=True)
class StaleReport:
    """``rebuild``: build dirs to remove; ``disabled``: packages turned off."""

    rebuild: tuple[Path, ...] = ()
    disabled: tuple[str, ...] = ()
    changed: tuple[str, ...] = field(default=())


def stale_packages(old_text: str, new_text: str, build: Path) -> StaleReport:
    old, new = parse_config(old_text), parse_config(new_text)
    changed = changed_symbols(old, new)
    enabled = {s[len(PREFIX):] for s, v in new.items() if s.startswith(PREFIX) and v == "y"}
    was_enabled = {s[len(PREFIX):] for s, v in old.items() if s.startswith(PREFIX) and v == "y"}
    disabled = sorted(was_enabled - enabled)
    dirs = _build_dirs(build)

    built = {stem: path for stem in enabled if (path := _dir_for(stem, dirs)) is not None}
    rebuild: dict[str, Path] = {}
    for symbol in changed:
        stem = owning_package(symbol, set(built))
        if stem is not None:
            rebuild[stem] = built[stem]
    return StaleReport(
        rebuild=tuple(rebuild[k] for k in sorted(rebuild)),
        disabled=tuple(d.lower().replace("_", "-") for d in disabled if _dir_for(d, dirs)),
        changed=tuple(sorted(changed)),
    )


def parse_show_info(text: str) -> dict[str, set[str]]:
    """``make show-info`` JSON -> ``{package: {dependencies}}``."""
    info = json.loads(text)
    return {name: set(entry.get("dependencies") or ()) for name, entry in info.items()}


#: Dependencies that change how a package is built, never what it builds.
#: ``BR2_CCACHE`` adds host-ccache to every package's dependency list; counting
#: it would dirclean every built package in the tree (a full rebuild) just for
#: turning the compiler cache on or off.
BUILD_TOOL_DEPS = frozenset({"host-ccache"})


def dependency_changes(
    old_deps: dict[str, set[str]], new_deps: dict[str, set[str]], build: Path
) -> tuple[Path, ...]:
    """Build dirs of built packages whose resolved dependency set changed
    (ignoring :data:`BUILD_TOOL_DEPS`)."""
    dirs = _build_dirs(build)
    stale = []
    for name in sorted(old_deps.keys() & new_deps.keys()):
        if old_deps[name] - BUILD_TOOL_DEPS == new_deps[name] - BUILD_TOOL_DEPS:
            continue
        path = _dir_for(_normalize(name), dirs)
        if path is not None:
            stale.append(path)
    return tuple(stale)


def remove_build_dirs(paths: tuple[Path, ...]) -> None:
    """``make <pkg>-dirclean`` for each: drop the build dir so it rebuilds."""
    for path in paths:
        if path.exists():
            shutil.rmtree(path)
