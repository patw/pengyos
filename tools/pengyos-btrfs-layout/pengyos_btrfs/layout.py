"""The PengyOS canonical btrfs subvolume layout, defined as data.

This module is the single source of truth for *what* the layout is: an
immutable, named collection of subvolumes (the canonical PengyOS tree — ``@``,
``@home``, ``@var``, ``@snapshots``), with validation. Later tasks build on
this data: command generation (``commands.py``), the ``plan`` handler (which
serializes a :class:`Layout` to ``layout.toml``), and snapshot/rollback
planning.

Design rules (recorded in ``spec.md`` — "Layout (data + validation)"):

- A :class:`Subvolume` is an immutable record: ``name`` (the subvolume
  component, e.g. ``"@home"``), ``path`` (``"@"`` for top-level, or
  ``"/@x"`` for siblings under top-level), ``read_only`` (``ro=1`` at mount
  time), ``mount`` (the target directory under the filesystem's mount point,
  ``"/"`` for ``@``, ``"/home"`` for ``@home``; ``None`` for subvolumes that
  are not mounted, e.g. ``@snapshots``) and ``description``.
- The canonical layout is *flat*: every subvolume is a sibling directly under
  the top-level subvolume, so mount ordering stays trivial and no live
  subvolume ever nests inside another.
- :func:`canonical_layout` returns the canonical PengyOS layout as a frozen
  :class:`Layout`.
- :func:`validate_layout` enforces every structural rule and returns the
  layout, or raises :class:`pengyos_btrfs.errors.LayoutError` with a precise,
  actionable message. Malformed input never surfaces as a traceback.

Pure standard library; no I/O, no ``btrfs`` calls — this is data + validation
only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from . import constants as c
from .errors import LayoutError

# A subvolume component: "@" or "@" followed by [A-Za-z0-9._-] and nothing
# else (no spaces, no "/" — the path is built from components, not free text).
_SUBVOL_NAME_RE = re.compile(r"^@([A-Za-z0-9._-]*)$")
# A mount target: "/" or "/<component>(/<component>)*" where a component is
# [A-Za-z0-9._-] — relative, anchored, no "."/".." escapes.
_MOUNT_RE = re.compile(r"^/(?:[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*)?$")


@dataclass(frozen=True)
class Subvolume:
    """One planned btrfs subvolume.

    Attributes:
        name: the subvolume component, e.g. ``"@"`` or ``"@home"``.
        path: the subvolume's full path under the filesystem: ``"@"`` for the
            top-level itself, or ``"/@x"`` for a sibling under it.
        read_only: mounted read-only when ``True`` (``mount -o ro``).
        mount: the mount target under the filesystem's mount point
            (``"/"`` for ``@``, ``"/home"`` for ``@home``, ...), or ``None``
            for subvolumes that are not mounted at all (e.g. ``@snapshots``,
            which is merely a directory the root subvolume exposes).
        description: a one-line human description of the subvolume's role.
    """

    name: str
    path: str
    read_only: bool = False
    mount: Optional[str] = "/"
    description: str = ""


@dataclass(frozen=True)
class Layout:
    """An immutable plan of subvolumes (creation order)."""

    filesystem: str
    subvolumes: tuple  # tuple[Subvolume, ...]
    snapshot_dir: str = c.SNAPSHOT_DIR

    def index_by_path(self) -> "dict":
        """Map ``path`` -> :class:`Subvolume`."""
        return {s.path: s for s in self.subvolumes}

    def find(self, path: str) -> "Subvolume":
        """Return the subvolume at *path*, or raise :class:`LayoutError`."""
        by_path = self.index_by_path()
        if path not in by_path:
            known = ", ".join(sorted(by_path))
            raise LayoutError(
                f"no subvolume at path {path!r} in layout {self.filesystem!r} "
                f"(known paths: {known})"
            )
        return by_path[path]

    def snapshot_subvolume(self) -> "Subvolume":
        """The subvolume that hosts named snapshots (``@snapshots``)."""
        for s in self.subvolumes:
            if s.name == self.snapshot_dir:
                return s
        raise LayoutError(
            f"layout {self.filesystem!r} has no snapshot subvolume named "
            f"{self.snapshot_dir!r}"
        )

    def to_dict(self) -> "dict":
        """Plain-dict form for serialization (``plan --out``) and golden tests."""
        return {
            "filesystem": self.filesystem,
            "snapshot_dir": self.snapshot_dir,
            "subvolumes": [
                {
                    "name": s.name,
                    "path": s.path,
                    "read_only": s.read_only,
                    "mount": s.mount,
                    "description": s.description,
                }
                for s in self.subvolumes
            ],
        }


def _subvol_parts(path: str) -> "list":
    """Split a subvolume path into its components (``"/@x"`` ->
    ``["@x"]``; ``"@"`` -> ``["@"]``)."""
    return [p for p in path.split("/") if p]


def _valid_subvol_name(name: str, context: str) -> None:
    if not _SUBVOL_NAME_RE.match(name):
        raise LayoutError(
            f"{context}: invalid subvolume name {name!r} — a name must be "
            "'@' or '@' followed by letters, digits, '.', '_' or '-'"
        )


def _valid_mount(mount: str, context: str) -> None:
    if not _MOUNT_RE.match(mount):
        raise LayoutError(
            f"{context}: invalid mount target {mount!r} — it must be '/' or "
            "'/component[/component...]' (relative, anchored, no '.' or '..')"
        )
    for comp in (mount.strip("/").split("/") if mount.strip("/") else []):
        if comp in (".", ".."):
            raise LayoutError(
                f"{context}: invalid mount target {mount!r} — components may "
                "not be '.' or '..'"
            )


def validate_layout(
    filesystem: str,
    subvolumes: Sequence[Subvolume],
    snapshot_dir: str = c.SNAPSHOT_DIR,
) -> Layout:
    """Validate *subvolumes* for *filesystem* and return a frozen
    :class:`Layout`.

    Raises:
        LayoutError: if any rule is violated (see the checks below). The
            message names the offending path/name so the fix is actionable.
    """
    if not isinstance(filesystem, str) or not filesystem.strip():
        raise LayoutError("filesystem must be a non-empty string")
    if filesystem.startswith("/"):
        raise LayoutError(
            f"filesystem must be a filesystem name, not a path: {filesystem!r}"
        )
    if len(filesystem) > 255:
        raise LayoutError(
            f"filesystem name is longer than 255 chars: {filesystem!r}"
        )

    if not isinstance(snapshot_dir, str) or not _SUBVOL_NAME_RE.match(
        snapshot_dir
    ):
        raise LayoutError(
            f"snapshot_dir must be a valid subvolume name like "
            f"{c.SNAPSHOT_DIR!r}, got {snapshot_dir!r}"
        )

    if not subvolumes:
        raise LayoutError(
            f"layout {filesystem!r} must contain at least one subvolume"
        )

    for i, s in enumerate(subvolumes):
        if not isinstance(s, Subvolume):
            raise LayoutError(
                f"subvolume #{i} is {type(s).__name__}, expected Subvolume"
            )
        ctx = f"subvolume {s.name!r}"
        _valid_subvol_name(s.name, ctx)
        if not s.path or (s.path != "@" and not s.path.startswith("/")):
            raise LayoutError(
                f"{ctx}: path must be '@' or start with '/', got {s.path!r}"
            )
        if s.mount is not None:
            _valid_mount(s.mount, ctx)
        if not isinstance(s.read_only, bool):
            raise LayoutError(
                f"{ctx}: read_only must be a bool, got {s.read_only!r}"
            )

    # --- Uniqueness ------------------------------------------------------
    names = [s.name for s in subvolumes]
    for n in names:
        if names.count(n) > 1:
            raise LayoutError(f"duplicate subvolume name {n!r}")
    paths = [s.path for s in subvolumes]
    for p in paths:
        if paths.count(p) > 1:
            raise LayoutError(f"duplicate subvolume path {p!r}")
    mounts = [s.mount for s in subvolumes if s.mount is not None]
    for m in mounts:
        if mounts.count(m) > 1:
            raise LayoutError(f"duplicate mount target {m!r}")

    # --- Flat tree: paths are '@' or '/<name>', first component == name ---
    for s in subvolumes:
        parts = _subvol_parts(s.path)
        if not parts:
            raise LayoutError(f"subvolume {s.name!r}: empty path")
        for part in parts:
            _valid_subvol_name(part, f"subvolume {s.name!r} (path {s.path!r})")
        if len(parts) > 1:
            raise LayoutError(
                f"subvolume {s.name!r} at {s.path!r}: nested subvolumes are not "
                f"allowed in the canonical layout — every subvolume is a "
                f"sibling directly under the top-level ('@')"
            )
        if parts[0] != s.name:
            raise LayoutError(
                f"subvolume {s.name!r}: path {s.path!r} must start with its "
                f"own name (first path component is {parts[0]!r})"
            )

    # --- Mount targets must not shadow each other -------------------------
    # Any subvolume mounted under "/" is fine (e.g. /home is a directory
    # inside the root mount) — the root mount is the filesystem itself.
    # Non-root mounts may not nest: mounting /home and also /home/x would
    # hide part of the /home subvolume.
    for a in subvolumes:
        for b in subvolumes:
            if a.path == b.path or a.mount is None or b.mount is None:
                continue
            if a.mount == "/" or b.mount == "/":
                continue
            if a.mount != b.mount and (
                a.mount.startswith(b.mount.rstrip("/") + "/")
                or b.mount.startswith(a.mount.rstrip("/") + "/")
            ):
                raise LayoutError(
                    f"mount targets shadow each other: {a.mount!r} ({a.path}) "
                    f"and {b.mount!r} ({b.path})"
                )

    # --- The snapshot dir must exist so snapshots have a parent -----------
    if not any(s.name == snapshot_dir for s in subvolumes):
        raise LayoutError(
            f"layout {filesystem!r} must include a {snapshot_dir!r} subvolume "
            f"to host named snapshots"
        )

    return Layout(
        filesystem=filesystem,
        subvolumes=tuple(subvolumes),
        snapshot_dir=snapshot_dir,
    )


def canonical_layout(filesystem: str = "pengyos") -> Layout:
    """The canonical PengyOS layout, already validated.

    Layout (flat: every subvolume is a sibling directly under top-level):

    ============  ==========  =====  =========  =================================
    name          path        ro     mount      role
    ============  ==========  =====  =========  =================================
    ``@``         ``@``       no     ``/``      OS root — the frozen base image
    ``@home``     ``/@home``  no     ``/home``  user homes (mutable layer)
    ``@var``      ``/@var``   no     ``/var``   logs/state (mutable)
    ``@snapshots`` ``/@snapshots``  yes  (none)  snapshot parent directory
    ============  ==========  =====  =========  =================================

    ``@snapshots`` is not mounted itself (``mount=None``): it is a directory
    inside the root subvolume that hosts read-only named snapshots, so a
    snapshot can never shadow a live mount point.
    """
    subvolumes = (
        Subvolume(
            name="@",
            path="@",
            read_only=False,
            mount="/",
            description=(
                "OS root; the frozen base image (agent mutation lands in "
                "home/var, never here)"
            ),
        ),
        Subvolume(
            name="@home",
            path="/@home",
            read_only=False,
            mount="/home",
            description="user homes; the mutable, agent-editable layer",
        ),
        Subvolume(
            name="@var",
            path="/@var",
            read_only=False,
            mount="/var",
            description="logs and state; mutable, snapshot-covered",
        ),
        Subvolume(
            name="@snapshots",
            path="/" + c.SNAPSHOT_DIR,
            read_only=True,
            mount=None,
            description=(
                "parent directory for read-only named snapshots (never "
                "shadowed by a live mount point)"
            ),
        ),
    )
    return validate_layout(filesystem, subvolumes)


__all__ = [
    "Layout",
    "Subvolume",
    "canonical_layout",
    "validate_layout",
]
