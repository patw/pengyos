"""Reading hardware facts from a *source directory*.

The collectors (see :mod:`pengyos_hwprofile.collectors`) never touch the live
system directly. Instead they read through a :class:`Source`, which is a thin,
read-only view over a directory tree. In tests the tree is a committed fixture
under ``tests/fixtures/sys``; in production the same layout mirrors the parts of
``/sys`` (and ``/proc``) the collectors care about.

Keeping every read behind :class:`Source` gives three things:

* **Testability** — no root, no network, no real hardware. A fixture tree is a
  plain directory, so ``pytest`` runs anywhere.
* **Determinism** — the source is a fixed on-disk tree, never a live mount that
  changes between runs.
* **One seam for the CLI** — the ``detect`` subcommand opens a directory source
  (``--from`` or ``/sys``) today; the later integration work can point the very
  same :class:`Source` at command output without rewriting the collectors.

The design is intentionally small: a :func:`open_source` factory and a
:class:`Source` with a single :meth:`Source.read` primitive. Everything else in
the package builds on that primitive.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Union


class SourceError(OSError):
    """Raised when a source cannot be opened or a path cannot be read.

    Subclasses :class:`OSError` so callers that already guard against I/O
    errors (and the live-``/sys`` case, which is real I/O) keep working.
    """


class Source:
    """A read-only view over a source directory.

    Paths are given *relative* to the source root (e.g.
    ``"class/dmi/id/sys_vendor"``); :class:`Source` joins them against the root
    and enforces that the resolved path stays inside the root. This mirrors how
    the collectors think about ``/sys`` — by relative path — while keeping reads
    sandboxed to the directory they were given.

    A :class:`Source` is not thread-safe beyond the guarantees of the underlying
    filesystem; the collectors are single-threaded, so that is a non-issue.
    """

    __slots__ = ("_root",)

    def __init__(self, root: Union[str, "os.PathLike[str]"]) -> None:
        self._root = Path(root).resolve()

    @property
    def root(self) -> Path:
        """The absolute, resolved root directory of this source."""

        return self._root

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}({str(self._root)!r})"

    def _resolve(self, relpath: str) -> Path:
        """Resolve ``relpath`` against the root, rejecting escapes.

        Raises :class:`SourceError` if the resolved path is not inside the root
        (a ``..`` traversal attempt) or is not a regular file.
        """

        # ``PurePath`` normalises ``..`` *lexically*; only then do we compare
        # against the resolved root so a traversal cannot escape it.
        resolved = (self._root / relpath)
        resolved = resolved.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise SourceError(f"path escapes source root: {relpath!r}") from None

        if not resolved.is_file():
            raise SourceError(f"no such file in source: {relpath!r}")
        return resolved

    def read(self, relpath: str) -> str:
        """Read a file from the source, returning its text without trailing newline.

        The returned string has a single trailing ``\\n`` (and only ``\\n``)
        stripped, matching how sysfs files present their contents. This keeps
        parsers simple — ``read("class/dmi/id/sys_vendor")`` yields
        ``"Micro-Star International Co., Ltd."`` rather than ``"...\n"``.
        """

        path = self._resolve(relpath)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise SourceError(f"cannot read {relpath!r}: {exc}") from exc
        if text.endswith("\n"):
            text = text[:-1]
        return text

    def read_bytes(self, relpath: str) -> bytes:
        """Read a file from the source as raw bytes (no newline stripping).

        Used only where the trailing newline is significant; most collectors use
        :meth:`read`.
        """

        path = self._resolve(relpath)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise SourceError(f"cannot read {relpath!r}: {exc}") from exc


def open_source(root: Union[str, "os.PathLike[str]"]) -> Source:
    """Open ``root`` as a :class:`Source`.

    ``root`` is the directory to read from — a fixture tree in tests, ``/sys`` on
    a real machine, or any directory shaped like it. Raises :class:`SourceError`
    if the directory does not exist or is not a directory.
    """

    path = Path(root).resolve()
    if not path.exists():
        raise SourceError(f"source does not exist: {root!r}")
    if not path.is_dir():
        raise SourceError(f"source is not a directory: {root!r}")
    return Source(path)


# ---------------------------------------------------------------------------
# The live system.
#
# The collectors read a *source directory* laid out like a combined ``/sys`` +
# ``/proc`` tree, using *relative* paths such as ``proc/cpuinfo``,
# ``class/dmi/id/sys_vendor`` and ``bus/pci/devices/<bus>/class``. On a live
# machine ``/proc`` and ``/sys`` are separate pseudo-filesystem mounts, so a
# single :class:`Source` root cannot cover both. :class:`LiveSource` bridges the
# two: it maps the collectors' relative paths onto the real roots so the very
# same collectors read the live machine through the same code path as a fixture.
# ---------------------------------------------------------------------------

# The relative path prefixes the collectors use, and where each maps on a live
# machine. Anything under ``proc/`` comes from ``/proc``; ``class/`` and ``bus/``
# come from ``/sys`` (whose ``class`` and ``bus`` subdirectories the collectors
# address directly, mirroring the committed fixture tree).
_LIVE_ROOTS: tuple[tuple[str, Path], ...] = (
    ("proc", Path("/proc")),
    ("class", Path("/sys/class")),
    ("bus", Path("/sys/bus")),
)


class LiveSource(Source):
    """A :class:`Source` that reads the live ``/proc`` + ``/sys``.

    Relative paths are mapped onto the real pseudo-filesystem roots (see
    ``_LIVE_ROOTS``). Unlike :class:`Source`, :meth:`_resolve` does not enforce
    that the resolved path stays inside a sandbox root — the live tree is the
    sanctioned read surface, and its symlinks legitimately point outside any
    throwaway directory.

    Raises :class:`SourceError` if ``/proc`` or ``/sys`` cannot be read, so the
    ``detect`` subcommand can report a clear, actionable message.
    """

    __slots__ = ()

    def __init__(self) -> None:
        # ``_read_link`` / ``_sata_bus`` / ``_pci_bus_for_net`` resolve symlinks
        # via ``source.root / relpath``; every symlink the collectors follow is
        # under ``class/`` or ``bus/`` on the live machine, so ``/sys`` is the
        # base they expect.
        object.__setattr__(self, "_root", Path("/sys"))

    def _resolve(self, relpath: str) -> Path:
        """Map ``relpath`` onto the live root for its prefix.

        ``proc/cpuinfo`` -> ``/proc/cpuinfo``; ``class/dmi/id/sys_vendor`` ->
        ``/sys/class/dmi/id/sys_vendor``; ``bus/pci/...`` -> ``/sys/bus/pci/...``.
        A path with no recognised prefix is rejected, so a typo cannot silently
        read the wrong file.
        """

        parts = relpath.split(os.sep, 1)
        prefix = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        for prefix_root, live_root in _LIVE_ROOTS:
            if prefix == prefix_root:
                target = live_root / rest
                if not target.is_file():
                    raise SourceError(f"no such file in live source: {relpath!r}")
                return target
        raise SourceError(f"unknown source prefix in {relpath!r}: {prefix!r}")


def live_source() -> LiveSource:
    """Open the live ``/proc`` + ``/sys`` as a :class:`LiveSource`.

    Raises :class:`SourceError` if either pseudo-filesystem root is unreadable
    (e.g. a non-Linux host, or a kernel without the sysfs/procfs the collectors
    need).
    """

    for _, live_root in _LIVE_ROOTS:
        if not live_root.exists():
            raise SourceError(f"live source root missing: {live_root!r}")
    return LiveSource()
