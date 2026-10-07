"""Build a deterministic gzip-compressed ``newc`` cpio initramfs.

``build_archive(spec, root)`` walks ``Spec.all_paths()`` (already sorted and
de-duplicated), reads each file from the root directory, and renders a
byte-identical ``.cpio.gz`` archive:

* **Order** — entries are written in sorted path order, one archive position
  per spec path.
* **Ownership** — every entry gets the spec's normalized ``uid``/``gid``.
* **mtime** — every entry gets the spec's normalized ``mtime``; the wall
  clock and filesystem timestamps are never read.
* **Modes** — normalized per include kind (not copied from the checkout):
  ``binaries`` → ``0o100755``, ``files``/``modules`` → ``0o100644``,
  symlinks → ``0o120777`` (conventional),
  ``directories`` → ``0o040755``.
* **inode numbers** — 1-based position in the archive (see ``cpio.py``).
* **Gzip container** — zeroed gzip mtime, empty FNAME, fixed compression
  level, so the ``.cpio.gz`` bytes are a pure function of the inputs.
* **``/init``** — the archive always contains the spec's ``init.path`` (default
  ``"init"``) as a generated minimal POSIX ``sh`` script (see
  :mod:`.initgen`) packed with mode ``0o100000 | spec.init_mode`` (default
  ``0o100755``) in its canonical sorted position. An operator path that is
  *explicitly* listed in an include list wins over the generated script (the
  real file from the root is packed with the kind's normalized mode).

The archive contains exactly the spec'd paths plus the generated init entry —
no auto-added parent directories. Paths listed in the spec's
``directories`` list are packed as cpio *directory* entries (name with a
trailing ``/``, empty data, mode ``0o040755``); the kernel's initramfs
populate creates them during extraction, so nested file paths extract
without a missing-parent-directory error. If a path is listed both in
``directories`` and in a file kind, ``directories`` wins (it is the only
kind that can represent a directory).
"""

from __future__ import annotations

import gzip
import io
import os
from pathlib import Path

from . import cpio
from .initgen import init_entry_bytes, init_entry_mode
from .spec import Spec

__all__ = ["BuildError", "build_archive", "gzip_compress", "KIND_MODES"]


class BuildError(Exception):
    """Raised when a spec path cannot be turned into an archive entry."""


# Normalized modes per include kind (file-type bits included).
KIND_MODES: dict[str, int] = {
    "binaries": 0o100755,
    "files": 0o100644,
    "modules": 0o100644,
    "directories": 0o040755,
}

_SYMLINK_MODE = 0o120777


def _kind_for(path: str, spec: Spec) -> str:
    """Dedup across kinds: the first listing wins.

    Precedence: directories > binaries > files > modules. ``directories``
    goes first because it is the only kind that can represent a directory
    on disk; the file kinds are unchanged relative to each other.
    """
    for kind in ("directories", "binaries", "files", "modules"):
        if path in spec.paths_for(kind):
            return kind
    raise AssertionError(f"internal error: {path!r} not in any spec include list")


def gzip_compress(data: bytes, *, mtime: int = 0, compresslevel: int = 9) -> bytes:
    """Deterministically gzip *data*: zeroed gzip mtime, no FNAME, fixed level."""
    # filename="" keeps the FNAME header field empty and mtime=0 zeroes the
    # MTIME header field, so the output depends only on `data` + settings.
    buf = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=buf, compresslevel=compresslevel, mtime=mtime
    ) as gz:
        gz.write(data)
    return buf.getvalue()


def build_archive(spec: Spec, root: Path | str) -> bytes:
    """Build the gzip cpio archive for *spec* from files under *root*.

    Returns the complete ``.cpio.gz`` bytes. Raises :class:`BuildError` if a
    spec path is missing under *root* or is of an unsupported type.
    """
    root = Path(root)
    if not root.is_dir():
        raise BuildError(f"root is not a directory: {root}")

    writer = cpio.CpioWriter(uid=spec.uid, gid=spec.gid, mtime=spec.mtime)

    included = set(spec.all_paths())
    paths = sorted(included | {spec.init_path})

    for path in paths:
        if path == spec.init_path and path not in included:
            writer.add_file(path, init_entry_bytes(spec), mode=init_entry_mode(spec))
            continue
        src = root / path
        if path in spec.directories:
            # Explicit directory entry. A symlink to a directory is accepted
            # (is_dir follows it); the packed entry is always a plain
            # directory (the kernel only needs the path to exist as a dir).
            if not src.is_dir():
                raise BuildError(
                    f"{path}: listed in 'directories' but is not a directory "
                    f"under root {root}"
                )
            writer.add_directory(path, mode=KIND_MODES["directories"])
        elif src.is_symlink():
            writer.add_symlink(path, os.readlink(src), mode=_SYMLINK_MODE)
        elif src.is_dir():
            raise BuildError(
                f"{path}: is a directory; the archive contains exactly the spec'd "
                "paths — list it in the spec's 'directories' to pack it as a "
                "directory entry"
            )
        elif src.is_file():
            try:
                data = src.read_bytes()
            except OSError as exc:
                raise BuildError(f"{path}: cannot read: {exc}") from exc
            writer.add_file(path, data, mode=KIND_MODES[_kind_for(path, spec)])
        else:
            raise BuildError(
                f"{path}: not found under root {root} (or unsupported file type)"
            )

    return gzip_compress(writer.archive())
