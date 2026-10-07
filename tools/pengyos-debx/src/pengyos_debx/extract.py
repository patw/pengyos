"""Extract a package payload into an isolated install prefix.

This module implements the *extract* step of the debx tool: it writes a
``.deb``/``.rpm`` payload to a caller-chosen prefix, recreating regular files,
directories and symlinks, and records a manifest (path + size + SHA-256) of what
it wrote.

Security invariants (the whole point of the tool):

* **Maintainer scripts are never executed.** Extraction only touches the payload
  tarball (``data.tar.*`` for deb, ``payload.tar.*`` for rpm). The ``control``
  tarball — which holds ``postinst``/``postrm``/… — is never read here, let
  alone run. :func:`read_package` never opens it for execution.
* **Path traversal is refused.** A payload path that would resolve outside the
  prefix (via ``..`` or an absolute path) raises :class:`PathEscapeError`
  rather than writing anywhere it should not.
* **Nothing is executed.** Writing bytes and creating symlinks is not execution;
  no script interpreter is invoked.

The public surface is small on purpose:

* :class:`PathEscapeError`   -- a payload path escapes the prefix.
* :class:`ExtractedEntry`    -- one written file/dir/symlink.
* :class:`ExtractResult`     -- the full result of an extraction.
* :func:`extract_package`    -- the extraction entry point.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional

from .formats import (
    PAYLOAD_DIR,
    PAYLOAD_FILE,
    PAYLOAD_SYMLINK,
    PayloadMember,
    read_package,
)

# Exit code for a payload that tries to escape the install prefix. Kept in sync
# with ``cli.py``.
EXIT_PATH_ESCAPE = 5


class PathEscapeError(Exception):
    """A payload path would resolve outside the install prefix."""


@dataclass(frozen=True)
class ExtractedEntry:
    """One entry written by :func:`extract_package`.

    ``sha256`` is the hex digest of the file contents for regular files, or
    ``None`` for directories and symlinks. ``link_target`` is set only for
    symlinks.
    """

    name: str
    kind: str
    size: int
    sha256: Optional[str]
    link_target: Optional[str] = None


@dataclass
class ExtractResult:
    """The outcome of an extraction: the prefix and every written entry."""

    source: str
    into: str
    entries: list[ExtractedEntry] = field(default_factory=list)

    @property
    def files(self) -> list[ExtractedEntry]:
        """The regular files written (``kind == 'file'``)."""
        return [e for e in self.entries if e.kind == PAYLOAD_FILE]

    def manifest(self) -> Dict[str, str]:
        """A ``{package-path: sha256}`` map for the regular files written."""
        return {e.name: e.sha256 for e in self.entries if e.sha256 is not None}

    def summary(self) -> str:
        """A one-line human summary: counts of files/dirs/symlinks."""
        counts: Dict[str, int] = {PAYLOAD_FILE: 0, PAYLOAD_DIR: 0, PAYLOAD_SYMLINK: 0}
        for e in self.entries:
            counts[e.kind] = counts.get(e.kind, 0) + 1
        return (
            f"extracted {counts[PAYLOAD_FILE]} file(s), "
            f"{counts[PAYLOAD_DIR]} dir(s), "
            f"{counts[PAYLOAD_SYMLINK]} symlink(s) into {self.into}"
        )


def _resolve_target(prefix: str, name: str) -> str:
    """Return the absolute on-disk path for payload member ``name`` under ``prefix``.

    Rejects absolute paths and ``..`` traversal that would escape ``prefix``.
    The payload paths are absolute (e.g. ``/usr/lib/libfoo.so.1``); the leading
    slash is dropped and the remainder is joined to the prefix.
    """
    rel = name.lstrip("/")
    # Reject any component that climbs out of the prefix.
    parts: list[str] = []
    for component in rel.split(os.sep):
        if component in ("", "."):
            continue
        if component == "..":
            raise PathEscapeError(
                f"payload path {name!r} escapes the install prefix"
            )
        parts.append(component)
    target = os.path.normpath(os.path.join(prefix, *parts))
    # Defence in depth: ``target`` must be ``prefix`` itself or live inside it.
    if target != prefix and not target.startswith(prefix + os.sep):
        raise PathEscapeError(f"payload path {name!r} escapes the install prefix")
    return target


def extract_package(
    package: Any,
    into: str,
    *,
    on_conflict: str = "fail",
) -> ExtractResult:
    """Extract ``package``'s payload into the isolated prefix ``into``.

    Args:
        package: a :class:`~pengyos_debx.formats.DebPackage` or
            :class:`~pengyos_debx.formats.RpmPackage` (anything exposing a
            ``.payload`` with an ``iter_members()`` method).
        into: the install prefix (created if missing).
        on_conflict: ``"fail"`` (refuse to overwrite an existing path) or
            ``"replace"`` (overwrite files, resym/symlink/dirmove the rest).

    Returns:
        An :class:`ExtractResult` recording every written entry.

    Raises:
        PathEscapeError: a payload path escapes ``into``.
    """
    into_abs = os.path.abspath(os.path.expanduser(into))
    os.makedirs(into_abs, exist_ok=True)

    result = ExtractResult(
        source=getattr(package, "source", ""), into=into_abs, entries=[]
    )

    for member in _iter_payload(package):
        target = _resolve_target(into_abs, member.name)
        result.entries.append(
            _write_entry(target, member, on_conflict=on_conflict)
        )
    return result


def _iter_payload(package: Any) -> Iterator[PayloadMember]:
    """Yield payload members, tolerating both payload objects and packages."""
    payload = getattr(package, "payload", package)
    return payload.iter_members()


def _write_entry(
    target: str, member: PayloadMember, *, on_conflict: str
) -> ExtractedEntry:
    """Write one payload member to ``target`` and return its :class:`ExtractedEntry`."""
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)

    if member.kind == PAYLOAD_DIR:
        # A directory member whose target already exists (e.g. it was created as
        # a parent directory while extracting a file) is not a conflict: ensure
        # it is present and move on. Only a non-directory at that path conflicts.
        if os.path.lexists(target) and not os.path.isdir(target):
            if on_conflict == "fail":
                raise FileExistsError(
                    f"{target!r} exists and is not a directory "
                    "(use on_conflict='replace')"
                )
            _remove(target)
        os.makedirs(target, exist_ok=True)
        return ExtractedEntry(member.name, PAYLOAD_DIR, 0, None)

    exists = os.path.lexists(target)
    if exists and on_conflict == "fail":
        raise FileExistsError(f"{target!r} already exists (use on_conflict='replace')")
    if exists:
        _remove(target)

    if member.kind == PAYLOAD_SYMLINK:
        target_str = member.data.decode("utf-8", "surrogateescape")
        os.symlink(target_str, target)
        return ExtractedEntry(
            member.name, PAYLOAD_SYMLINK, 0, None, link_target=target_str
        )

    # Regular file: write bytes and record the SHA-256.
    digest = hashlib.sha256()
    digest.update(member.data)
    with open(target, "wb") as fh:
        fh.write(member.data)
    return ExtractedEntry(
        member.name, PAYLOAD_FILE, len(member.data), digest.hexdigest()
    )


def _remove(target: str) -> None:
    """Remove a file, symlink or directory tree at ``target``."""
    if os.path.islink(target) or os.path.isfile(target):
        os.unlink(target)
    else:
        import shutil

        shutil.rmtree(target)


def extract_path(
    pkg_path: str,
    into: str,
    *,
    on_conflict: str = "fail",
) -> ExtractResult:
    """Convenience wrapper: read ``pkg_path`` and extract it into ``into``."""
    package = read_package(pkg_path)
    return extract_package(package, into, on_conflict=on_conflict)
