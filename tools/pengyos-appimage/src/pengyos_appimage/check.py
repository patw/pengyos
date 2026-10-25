"""``check`` — name the libraries an AppImage needs that this system lacks.

When an AppImage will not start, the error is often a step removed from the
cause: a Qt "could not load the platform plugin" dialog rather than "missing
libxcb-cursor.so.0". ``check`` answers the real question without running
anything:

1. walk the squashfs payload (pure Python, nothing written to disk);
2. read ``DT_NEEDED`` from every ELF object built for the runtime's
   architecture;
3. count a library as provided if the payload carries a file or symlink of that
   name (AppImages point ``LD_LIBRARY_PATH``/``RPATH`` at their own lib dirs) or
   the host's loader paths do;
4. add the libraries known to be ``dlopen``\\ ed by a bundled library, which
   ``DT_NEEDED`` cannot show (:data:`DLOPEN_DEPS`);
5. report what is left, with a hint per library where one is known.

It is an approximation of the dynamic loader, not a replica: it ignores symbol
versions (a binary built against a newer glibc than the host's still fails) and
treats every bundled library directory as on the search path.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import elf, runtime, squashfs

# ELF e_machine for each runtime architecture name runtime.py reports.
_MACHINE_FOR_ARCH = {"x86_64": 0x3E, "aarch64": 0xB7, "i386": 3, "armhf": 0x28}

# Libraries a bundled library loads with dlopen(), so DT_NEEDED never names
# them. Key: a library in the payload. Value: what it will try to load.
# Add a row when a real AppImage fails this way; say which app and why.
DLOPEN_DEPS: dict[str, tuple[str, ...]] = {
    # Qt >= 6.5's xcb platform plugin; without it Qt aborts with a platform-
    # plugin error. mdit (Qt 6.10) bundles it, many Qt 6 AppImages do not.
    "libQt6XcbQpa.so.6": ("libxcb-cursor.so.0",),
}

# Hints for missing libraries, matched by prefix. Keep them to cases where the
# answer is known; anything else gets the generic triage line.
HINTS: tuple[tuple[str, str], ...] = (
    ("libgtk-x11-2.0", "GTK 2 is not shipped on purpose: use the app's Flatpak, or run it in a guest (pengyos-guest)"),
    ("libgdk-x11-2.0", "GTK 2 is not shipped on purpose: use the app's Flatpak, or run it in a guest (pengyos-guest)"),
    ("libpython", "the app expects a system Python library; run it in a guest (pengyos-guest)"),
)
GENERIC_HINT = (
    "not on this system: if it is small linkage glue, it can be added to the "
    "image; otherwise use the app's Flatpak or run it in a guest (pengyos-guest)"
)

# Loader search paths beyond /etc/ld.so.conf. The image keeps everything in
# /usr/lib; the others make the tool useful on a development host too.
DEFAULT_LIBDIRS = ("/usr/lib", "/usr/lib64", "/lib", "/lib64")

# The dynamic loader is named by PT_INTERP, but some objects also list it.
_ALWAYS_PRESENT_PREFIXES = ("ld-linux", "ld64.so")


class CheckError(RuntimeError):
    """Raised when the AppImage cannot be read."""


@dataclass
class Missing:
    """One unresolved library."""

    soname: str
    #: Payload paths of (up to five) objects that need it.
    needed_by: list[str]
    #: True when it comes from DLOPEN_DEPS, not DT_NEEDED.
    dlopen: bool
    hint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "soname": self.soname,
            "needed_by": self.needed_by,
            "dlopen": self.dlopen,
            "hint": self.hint,
        }


@dataclass
class CheckResult:
    """The outcome of checking one AppImage."""

    appimage: str
    arch: str
    elf_count: int
    skipped_foreign: int
    missing: list[Missing] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, object]:
        return {
            "appimage": self.appimage,
            "arch": self.arch,
            "elf_count": self.elf_count,
            "skipped_foreign": self.skipped_foreign,
            "ok": self.ok,
            "missing": [m.to_dict() for m in self.missing],
            "unreadable": self.unreadable,
        }


def host_libraries(libdirs: list[str] | None = None) -> set[str]:
    """Return the file names in the loader's search path.

    With *libdirs* given, only those are used. Otherwise the defaults plus
    every directory in ``/etc/ld.so.conf`` (following ``include`` lines).
    """
    dirs = list(libdirs) if libdirs is not None else (
        list(DEFAULT_LIBDIRS) + _ld_so_conf_dirs(Path("/etc/ld.so.conf"))
    )
    names: set[str] = set()
    for d in dirs:
        try:
            names.update(os.listdir(d))
        except OSError:
            continue
    return names


def _ld_so_conf_dirs(conf: Path, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    try:
        lines = conf.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    dirs: list[str] = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("include"):
            pattern = line.split(None, 1)[1] if " " in line else ""
            if pattern and not pattern.startswith("/"):
                pattern = str(conf.parent / pattern)
            for inc in sorted(glob.glob(pattern)):
                dirs += _ld_so_conf_dirs(Path(inc), depth + 1)
        else:
            dirs.append(line)
    return dirs


def check_appimage(appimage: str | Path, libdirs: list[str] | None = None) -> CheckResult:
    """Check *appimage* against this system's libraries.

    Raises:
        CheckError: If the file is not a readable type-2 AppImage.
    """
    path = Path(appimage)
    if not path.is_file():
        raise CheckError(f"not a file: {path}")
    try:
        meta = runtime.read_metadata_file(path)
    except runtime.AppImageFormatError as exc:
        raise CheckError(str(exc)) from exc
    machine = _MACHINE_FOR_ARCH.get(meta.arch)
    host = host_libraries(libdirs)

    provided: set[str] = set()
    needs: dict[str, list[str]] = {}
    elf_count = foreign = 0
    unreadable: list[str] = []
    try:
        with squashfs.SquashFS.open(path, meta.payload_offset) as fs:
            for rel, inode in fs.walk():
                name = rel.rsplit("/", 1)[-1]
                if inode.is_symlink:
                    provided.add(name)
                    continue
                if not inode.is_file or inode.size < 64:
                    continue
                if not elf.is_elf(fs.read(inode, 4)):
                    continue
                provided.add(name)
                try:
                    info = elf.parse(fs.read(inode))
                except elf.ElfError:
                    unreadable.append(rel)
                    continue
                if info.elf_type not in (elf.ET_EXEC, elf.ET_DYN):
                    continue
                if machine is not None and info.machine != machine:
                    foreign += 1
                    continue
                elf_count += 1
                if info.soname:
                    provided.add(info.soname)
                for soname in info.needed:
                    needs.setdefault(soname, []).append(rel)
    except squashfs.SquashfsError as exc:
        raise CheckError(f"cannot read the payload: {exc}") from exc

    dlopen_needs: dict[str, list[str]] = {}
    for lib, wanted in DLOPEN_DEPS.items():
        if lib in provided:
            for soname in wanted:
                dlopen_needs.setdefault(soname, []).append(lib)

    missing: list[Missing] = []
    for table, is_dlopen in ((needs, False), (dlopen_needs, True)):
        for soname in sorted(table):
            if soname in provided or soname in host:
                continue
            if soname.startswith(_ALWAYS_PRESENT_PREFIXES):
                continue
            if any(m.soname == soname for m in missing):
                continue
            missing.append(Missing(
                soname=soname,
                needed_by=sorted(set(table[soname]))[:5],
                dlopen=is_dlopen,
                hint=_hint(soname),
            ))

    return CheckResult(
        appimage=str(path),
        arch=meta.arch,
        elf_count=elf_count,
        skipped_foreign=foreign,
        missing=missing,
        unreadable=unreadable,
    )


def _hint(soname: str) -> str:
    for prefix, hint in HINTS:
        if soname.startswith(prefix):
            return hint
    return GENERIC_HINT
