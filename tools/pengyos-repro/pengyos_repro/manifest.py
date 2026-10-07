"""Two-tree manifest: relative path -> (size, sha256), streaming and large-file safe.

This module is the foundation for the whole diff/score pipeline. It walks a single
build tree and produces a *manifest* — a mapping from each file's POSIX-relative
path to a :class:`FileEntry` recording its byte ``size`` and streaming ``sha256``.

Design goals
-------------------------------------------
* **Streaming / large-file safe.** Files are hashed in fixed-size chunks so that
  a multi-gigabyte artifact is never loaded fully into memory. The default chunk
  is 1 MiB.
* **Pure and testable.** :func:`manifest` and :func:`iter_manifest` take a root
  path and return/yield plain data; they have no I/O side effects beyond reading
  the tree under ``root``.
* **Regular files only.** Symlinks, directories and other special files are
  skipped (they are not hashable "artifacts"). This keeps the walk cycle-free and
  avoids following links out of the tree.

The next subtask (classification) will consume two :func:`manifest` results.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterator

# 1 MiB read buffer — large enough to be efficient, small enough to stay
# memory-bounded regardless of artifact size.
DEFAULT_CHUNK_SIZE = 1 << 20  # 1048576

# Raised when a file inside the tree cannot be read (permissions, vanished file,
# etc.). The CLI layer (later subtask) maps this to exit code 1.
class ManifestError(OSError):
    """A file within the tree could not be read or hashed."""


@dataclass(frozen=True)
class FileEntry:
    """A single artifact: its byte ``size`` and streaming ``sha256`` digest."""

    size: int
    sha256: str

    @property
    def hexdigest(self) -> str:
        """Alias for :attr:`sha256` (readable at call sites)."""
        return self.sha256


def _rel_posix(path: str, root: str) -> str:
    """Return ``path`` relative to ``root`` using POSIX (``/``) separators."""
    rel = os.path.relpath(path, root)
    return PurePosixPath(rel).as_posix()


def _hash_stream(path: str, chunk_size: int) -> FileEntry:
    """Stream ``path`` in chunks, returning ``(size, sha256)``.

    Reads at most ``chunk_size`` bytes at a time so memory use is constant
    regardless of file size. Raises :class:`ManifestError` on read failure.
    """
    digest = hashlib.sha256()
    size = 0
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:  # unreadable / vanished file
        raise ManifestError(f"cannot read {path!r}: {exc}") from exc
    return FileEntry(size=size, sha256=digest.hexdigest())


def iter_manifest(root: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[tuple[str, FileEntry]]:
    """Yield ``(relpath, FileEntry)`` for every regular file under ``root``.

    This is the lazy, streaming entry point: callers can classify or report
    entries as they arrive without materialising the whole tree in memory.
    Symlinks, directories and special files are skipped. Raises
    :class:`ManifestError` the first time an unreadable file is encountered.
    """
    root = os.fspath(root)
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            # Skip symlinks (and any non-regular file) — not hashable artifacts
            # and a potential source of cycles or escapes from the tree.
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            rel = _rel_posix(full, root)
            yield rel, _hash_stream(full, chunk_size)


def manifest(root: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict[str, FileEntry]:
    """Walk ``root`` and return a mapping of relative path -> :class:`FileEntry`.

    Convenience wrapper over :func:`iter_manifest` that materialises the full
    manifest. For very large trees prefer :func:`iter_manifest`.
    """
    return {rel: entry for rel, entry in iter_manifest(root, chunk_size=chunk_size)}
