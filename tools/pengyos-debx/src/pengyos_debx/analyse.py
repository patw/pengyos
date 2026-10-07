"""``DT_NEEDED`` analysis of an extracted prefix (pure ELF parsing).

This module owns the ``analyse`` command: it walks a prefix (or parses a single
ELF file) and reports the ``DT_NEEDED`` libraries each ELF object declares, then
**resolves** them against a set of ``--libdirs`` into a resolved-vs-missing
report. It uses **pure ELF header parsing** (see :mod:`pengyos_debx.elf`) — it
never invokes ``ldd`` (which runs the ELF's interpreter and can execute code)
and it never looks inside a libdir by executing anything: a library is merely
checked for *existence* on disk.

Resolution model:

* Each ``DT_NEEDED`` entry is a soname the loader matches **exactly** (version
  resolution is a non-goal, per spec §Non-goals). So a lib is *resolved* iff a
  file bearing that exact name exists in one of the libdirs — a plain
  ``os.path.lexists`` check, which also accepts a symlink with that name (the
  loader resolves a symlinked soname the same way). Nothing is executed.
* A lib is *missing* iff no libdir contains a file of that name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .elf import ElfInfo, parse_elf_file, scan_prefix


@dataclass(frozen=True)
class AnalysisEntry:
    """One ELF object and the ``DT_NEEDED`` libraries it declares."""

    path: str
    info: ElfInfo

    @property
    def dt_needed(self) -> List[str]:
        return list(self.info.dt_needed)


@dataclass(frozen=True)
class Resolution:
    """How one ELF object's declared libs resolve against the libdirs.

    Attributes:
        path: the analysed ELF object.
        resolved: the declared libs found (exact name) in a libdir.
        missing: the declared libs not found in any libdir.
        libdirs: the libdirs the resolution was performed against.
    """

    path: str
    resolved: Tuple[str, ...] = ()
    missing: Tuple[str, ...] = ()
    libdirs: Tuple[str, ...] = ()


@dataclass
class AnalysisReport:
    """The result of analysing a prefix (or single ELF file).

    Attributes:
        entries: one :class:`AnalysisEntry` per ELF object scanned.
        path: the analysed path (prefix or single file).
        libdirs: the ``--libdirs`` set passed by the caller. When non-empty,
            :meth:`resolution` classifies each declared lib as resolved (found
            in a libdir) or missing.
    """

    entries: List[AnalysisEntry]
    path: str
    libdirs: Optional[Sequence[str]] = None

    @property
    def files_scanned(self) -> int:
        """The number of ELF objects scanned."""
        return len(self.entries)

    @property
    def libdirs_tuple(self) -> Tuple[str, ...]:
        """The libdirs as a tuple (empty when none were supplied)."""
        return tuple(self.libdirs or ())

    def unique_needed(self) -> List[str]:
        """The sorted, de-duplicated union of every ``DT_NEEDED`` entry."""
        libs: set[str] = set()
        for entry in self.entries:
            libs.update(entry.dt_needed)
        return sorted(libs)

    def resolution(self) -> List[Resolution]:
        """Classify each object's declared libs as resolved vs missing.

        A lib is *resolved* iff a file of that exact name exists in one of the
        ``--libdirs`` (checked with :func:`os.path.lexists`, so a symlinked
        soname counts as present). Nothing is executed — this is a pure
        filesystem existence check. Returns one :class:`Resolution` per scanned
        object, in scan order; when no libdirs were supplied every entry is
        reported as fully missing (there is nothing to resolve against).
        """
        libdirs = self.libdirs_tuple
        if not libdirs:
            # Nothing to resolve against: report every declared lib as missing
            # so the caller still sees *what* is needed, just not *where*.
            return [
                Resolution(
                    path=e.path,
                    resolved=(),
                    missing=tuple(e.dt_needed),
                    libdirs=(),
                )
                for e in self.entries
            ]

        # A small cache of which names exist in which dir, so a lib declared by
        # several objects in the same prefix is checked only once per dir.
        dir_names: dict[str, set[str]] = {}
        for d in libdirs:
            names = dir_names.setdefault(d, set())
            if not names:
                try:
                    for entry in os.scandir(d):
                        names.add(entry.name)
                except OSError:
                    # A libdir that does not exist (or is unreadable) simply
                    # contributes nothing — the libs remain "missing".
                    pass

        results: List[Resolution] = []
        for entry in self.entries:
            resolved: List[str] = []
            missing: List[str] = []
            for name in entry.dt_needed:
                found = any(name in dir_names[d] for d in libdirs)
                (resolved if found else missing).append(name)
            results.append(
                Resolution(
                    path=entry.path,
                    resolved=tuple(resolved),
                    missing=tuple(missing),
                    libdirs=libdirs,
                )
            )
        return results

    def missing_libs(self) -> List[str]:
        """The sorted union of every declared lib that is missing anywhere.

        This is the "critical" list: a lib any object needs but that is absent
        from all libdirs. Empty when no libdirs were supplied (nothing to check
        against) — use :meth:`unique_needed` for that case.
        """
        if not self.libdirs_tuple:
            return []
        missing: set[str] = set()
        for res in self.resolution():
            missing.update(res.missing)
        return sorted(missing)

    def format(self) -> str:
        """Render the report for the terminal (deterministic, sorted by path)."""
        libdirs = self.libdirs_tuple
        header = (
            "Analysing DT_NEEDED in "
            f"{self.path} (pure ELF parsing; "
        )
        if libdirs:
            header += "resolved vs missing against libdirs):"
        else:
            header += "declared dependencies, NOT resolved):"
        lines = [header]

        for entry in sorted(self.entries, key=lambda e: e.path):
            rel = os.path.relpath(entry.path, self.path)
            deps = entry.dt_needed
            dep_word = "dependency" if len(deps) == 1 else "dependencies"
            lines.append(f"  {rel}")
            if deps:
                lines.append(
                    f"      {len(deps)} {dep_word}: " + ", ".join(deps)
                )
            else:
                lines.append("      (no dynamic dependencies)")

        lines.append("")
        unique = self.unique_needed()
        lib_word = "library" if len(unique) == 1 else "libraries"
        lines.append(
            f"Summary: {self.files_scanned} ELF file(s) scanned, "
            f"{len(unique)} unique DT_NEEDED {lib_word}."
        )

        if libdirs:
            res = self.resolution()
            total_resolved = sum(len(r.resolved) for r in res)
            total_missing = sum(len(r.missing) for r in res)
            missing = self.missing_libs()
            miss_word = "library" if len(missing) == 1 else "libraries"
            lines.append(
                f"  Resolved: {total_resolved} occurrence(s). "
                f"Missing: {total_missing} occurrence(s)."
            )
            if missing:
                lines.append("  Missing: " + ", ".join(missing))
            else:
                lines.append("  Missing: (none)")
            # Note any libdir that is not present on disk: the lookup could not
            # have found anything there, so "missing" may be a false positive.
            absent = [d for d in libdirs if not os.path.lexists(d)]
            if absent:
                lines.append(
                    "  Note: libdir(s) not present on disk: "
                    + ", ".join(absent)
                    + " (libs there are reported missing)."
                )
        elif self.entries:
            lines.append(
                "Note: no --libdirs given; the above lists declared "
                "DT_NEEDED entries only. Pass --libdirs to resolve "
                "resolved-vs-missing."
            )
        return "\n".join(lines)


def analyse_path(path: str, *, libdirs: Optional[Sequence[str]] = None) -> AnalysisReport:
    """Analyse ``path`` (a prefix directory or a single ELF file).

    * If ``path`` is a directory, every ELF object under it is scanned
      (see :func:`pengyos_debx.elf.scan_prefix`).
    * If ``path`` is a file, it is parsed directly with
      :func:`pengyos_debx.elf.parse_elf_file`.

    ``libdirs`` is the set of directories to resolve each object's declared
    ``DT_NEEDED`` against into a resolved-vs-missing report. A lib is *resolved*
    iff a file of that exact name exists in a libdir (a pure existence check —
    nothing is executed); otherwise it is *missing*. When no libdirs are given,
    every declared lib is reported as missing (there is nothing to resolve
    against).
    """
    if os.path.isdir(path):
        entries = [
            AnalysisEntry(p, info) for p, info in scan_prefix(path)
        ]
    else:
        info = parse_elf_file(path)
        entries = [AnalysisEntry(path, info)]
    return AnalysisReport(entries=entries, path=path, libdirs=libdirs)
