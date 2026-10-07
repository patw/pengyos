"""Backends for snapshot / restore in pengyos-mutations.

See :mod:`pengyos_mutations.spec` (``spec.md``) for the overall contract:
``snapshot -> apply -> record -> revert -> converge``.

This module defines the :class:`Backend` interface plus two implementations:

* :class:`MockBackend` — a copy-dir backend used for tests and local
  development. It requires no privileges and no special filesystem.
* :class:`BtrfsBackend` — a documented stub. Real btrfs snapshots require root
  privileges and a btrfs filesystem, so this is intentionally *not* a runtime
  dependency.

The CLI selects a backend by name via :func:`get_backend`.
"""

from __future__ import annotations

import abc
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

__all__ = [
    "Backend",
    "BackendError",
    "MockBackend",
    "BtrfsBackend",
    "Snapshot",
    "get_backend",
]


class BackendError(RuntimeError):
    """Raised when a backend cannot satisfy a request."""


@dataclass(frozen=True)
class Snapshot:
    """Metadata describing a recorded snapshot.

    Attributes
    ----------
    change_id:
        The stable identifier of the change the snapshot belongs to.
    backend:
        The name of the backend that produced the snapshot.
    entries:
        Maps a stable, caller-chosen key to the absolute path that was
        captured, or ``None`` when the path did not exist at snapshot time
        (i.e. it was created by the change and should be removed on revert).
    """

    change_id: str
    backend: str
    entries: Mapping[str, Optional[str]] = field(default_factory=dict)

    @property
    def keys(self) -> list[str]:
        """The stable keys that were tracked."""
        return list(self.entries)


class Backend(abc.ABC):
    """Abstract snapshot / restore backend.

    A backend captures the current state of a set of tracked filesystem paths
    (so a later change can be reverted) and restores them on demand.

    Subclasses MUST set :attr:`name` to the identifier used by :func:`get_backend`.
    """

    name: str = "base"

    def __init__(self, store: Path | str) -> None:
        self.store = Path(store)
        self.store.mkdir(parents=True, exist_ok=True)

    @abc.abstractmethod
    def snapshot(
        self, change_id: str, tracked: Mapping[str, Path]
    ) -> Snapshot:
        """Capture the current state of ``tracked`` under ``change_id``.

        Parameters
        ----------
        change_id:
            Stable identifier for the change being applied.
        tracked:
            Maps a stable key to the absolute path to track. A path that does
            not exist is recorded as ``None`` (created-by-change) so that
            :meth:`restore` can remove it later.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def restore(self, change_id: str, tracked: Mapping[str, Path]) -> None:
        """Restore ``tracked`` from the snapshot recorded under ``change_id``.

        Paths present in the snapshot are restored byte-for-byte (replacing any
        current content, including deletions). Paths that were absent at
        snapshot time are removed from the live tree.
        """
        raise NotImplementedError


def _remove_path(path: Path) -> None:
    """Remove a file, symlink, or directory tree at ``path``.

    Safe to call when ``path`` does not exist. Handles the case where the
    target is a directory that already exists on disk (``shutil.rmtree``
    refuses to operate on a path that is itself a directory).
    """
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _replace_path(dest: Path, src: Path) -> None:
    """Remove whatever is at ``dest`` and copy ``src`` into its place."""
    if dest.is_symlink() or dest.exists():
        _remove_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)


class MockBackend(Backend):
    """Copy-dir backend.

    Snapshots are plain copies of the tracked files (or directory trees) under
    ``<store>/mock/snapshots/<change_id>/``. No privileges or special
    filesystems are required, which makes it ideal for tests and local
    development.
    """

    name = "mock"

    def snapshot(
        self, change_id: str, tracked: Mapping[str, Path]
    ) -> Snapshot:
        snap_root = self.store / self.name / "snapshots" / change_id
        snap_root.mkdir(parents=True, exist_ok=True)

        entries: dict[str, Optional[str]] = {}
        for key, path in tracked.items():
            dest = snap_root / key
            resolved = Path(path).resolve()
            if resolved.exists():
                _replace_path(dest, resolved)
                entries[key] = str(dest)
            else:
                entries[key] = None
        return Snapshot(change_id=change_id, backend=self.name, entries=entries)

    def restore(self, change_id: str, tracked: Mapping[str, Path]) -> None:
        snap_root = self.store / self.name / "snapshots" / change_id
        if not snap_root.is_dir():
            raise BackendError(
                f"no {self.name!r} snapshot for change {change_id!r} in {self.store}"
            )

        for key, path in tracked.items():
            if path is None:
                # Nothing to restore; the caller tracked a path that no longer
                # exists, so there is no live file to revert.
                continue
            resolved = Path(path).resolve()
            src = snap_root / key
            if src.exists():
                # Present in the snapshot -> restore it, replacing whatever is
                # currently there (modified, deleted, or newly created).
                _replace_path(resolved, src)
            elif resolved.exists():
                # Absent at snapshot time (created by the change) -> remove it.
                _remove_path(resolved)


class BtrfsBackend(Backend):
    """Documented btrfs backend stub.

    A production btrfs backend would take copy-on-write snapshots of the tracked
    paths' parent subvolumes (``btrfs subvolume snapshot``) and restore them via
    subvolume rollback or ``btrfs`` send/receive. Those operations require root
    privileges and a real btrfs filesystem — both unavailable in the test
    environment — so this stub is intentionally *not* wired into the default
    flow.

    Instantiating it is fine; calling :meth:`snapshot` or :meth:`restore` raises
    :class:`BackendError` with guidance. See ``NEEDS.md`` if you need real
    btrfs support.
    """

    name = "btrfs"

    def _require(self) -> None:
        raise BackendError(
            "the 'btrfs' backend requires root privileges and a real btrfs "
            "filesystem; it is a documented stub, not a runtime dependency. "
            "Use the 'mock' backend for tests and local development."
        )

    def snapshot(
        self, change_id: str, tracked: Mapping[str, Path]
    ) -> Snapshot:
        self._require()

    def restore(self, change_id: str, tracked: Mapping[str, Path]) -> None:
        self._require()


_BACKENDS: dict[str, type[Backend]] = {
    MockBackend.name: MockBackend,
    BtrfsBackend.name: BtrfsBackend,
}


def get_backend(name: str, store: Path | str) -> Backend:
    """Return a backend instance for ``name`` backed by ``store``.

    Raises :class:`BackendError` for an unknown backend name.
    """
    try:
        cls = _BACKENDS[name]
    except KeyError:
        raise BackendError(
            f"unknown backend {name!r}; known backends: {sorted(_BACKENDS)}"
        ) from None
    return cls(store)
