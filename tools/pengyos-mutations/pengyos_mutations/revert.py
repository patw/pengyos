"""``revert`` — restore tracked files from a recorded snapshot.

:func:`revert_change` looks a recorded change up in the attest log, resolves the
backend and tracked root it used, and restores the tracked tree to its
pre-change state via the backend's :meth:`~pengyos_mutations.backends.Backend.restore`.

A change that *created* files has them removed; a change that *modified* or
*deleted* files has them restored. What a revert undid is reported in the
:class:`RevertResult` by diffing the tracked tree before and after the restore,
so the report reflects exactly what the backend put back (or took away) rather
than trusting a possibly-stale recorded diff.

The revert is itself appended to the attest log as a ``revert`` event so the
audit trail records both the mutation and its reversal.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pengyos_mutations import attest
from pengyos_mutations.apply import ROOT_KEY, change_id_now, iter_tree, walk_diff
from pengyos_mutations.backends import BackendError, get_backend

__all__ = [
    "RevertError",
    "RevertResult",
    "find_change",
    "revert_change",
]


class RevertError(RuntimeError):
    """Raised when a recorded change cannot be reverted."""


@dataclass
class RevertResult:
    """Outcome of :func:`revert_change`.

    Attributes
    ----------
    change_id:
        The change that was reverted.
    backend:
        The backend that performed the restore.
    root:
        The tracked root that was restored (absolute POSIX path).
    ok:
        ``True`` when the restore completed successfully.
    restored:
        Paths restored to their pre-change state: files the change deleted or
        modified that were brought back.
    removed:
        Paths the change created that were removed during the revert.
    """

    change_id: str
    backend: str
    root: str
    ok: bool
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """A one-line human-readable summary of what the revert did."""
        parts = [f"change {self.change_id} ({self.backend}) at {self.root}"]
        if self.restored:
            parts.append(f"restored {len(self.restored)} path(s): {', '.join(self.restored)}")
        if self.removed:
            parts.append(f"removed {len(self.removed)} path(s): {', '.join(self.removed)}")
        if not self.restored and not self.removed:
            parts.append("no tracked paths changed")
        return "; ".join(parts)


def _resolve_store(store: Optional[str]) -> Path:
    """The mutations store directory (honours ``$PENGYOS_MUTATIONS_STORE``)."""
    if store:
        return Path(store).expanduser()
    from pengyos_mutations.apply import default_store

    return default_store()


def _log_path(store: Optional[str]) -> Path:
    """The attest log for a store: ``<store>/events.jsonl``."""
    return _resolve_store(store) / "events.jsonl"


def _file_digest(path: Path) -> str:
    """A content digest for a file (``sha256`` of its bytes)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_change(store: Optional[str], change_id: str) -> Optional[dict]:
    """Return the apply record for ``change_id`` from the log, or ``None``.

    A record matches when its ``data.change_id`` equals ``change_id`` and its
    ``data.kind`` is ``"apply"``.
    """
    records = attest.read_records(_log_path(store))
    for rec in records:
        data = rec.get("data") or {}
        if data.get("change_id") == change_id and data.get("kind") == "apply":
            return rec
    return None


def revert_change(change_id: str, store: Optional[str] = None) -> RevertResult:
    """Restore the tracked files for the recorded change ``change_id``.

    The change is looked up in the attest log; its ``backend`` and tracked
    ``root`` are read from the apply record and the backend restores the tracked
    tree to its pre-change state. A :class:`RevertResult` reports what was
    restored and removed, and a ``revert`` event is appended to the log.

    Raises
    ------
    RevertError
        If the change is not recorded, the record has no tracked root, the
        backend cannot be loaded, or the restore fails.
    """
    record = find_change(store, change_id)
    if record is None:
        raise RevertError(f"no recorded change {change_id!r} in the attest log")

    data = record.get("data") or {}
    backend_name = data.get("backend") or "mock"
    root = data.get("root")
    if not root:
        raise RevertError(f"record {change_id!r} has no tracked root")

    store_path = _resolve_store(store)
    try:
        backend = get_backend(backend_name, store_path)
    except BackendError as exc:
        raise RevertError(f"cannot load backend {backend_name!r}: {exc}") from exc

    root_path = Path(root).expanduser().resolve()

    # Diff the tree before the restore so the report reflects reality.
    before = {rel: _file_digest(abs_path) for rel, abs_path in iter_tree(root_path)}

    try:
        backend.restore(change_id, {ROOT_KEY: root_path})
    except BackendError:
        raise
    except Exception as exc:  # pragma: no cover - defensive: surface cleanly
        raise RevertError(f"restore failed for {change_id!r}: {exc}") from exc

    after = {rel: _file_digest(abs_path) for rel, abs_path in iter_tree(root_path)}
    diff = walk_diff(root_path, before, after)
    # Created-by-change files are gone after restore (deleted in before/after);
    # deleted/modified-by-change files are back (created/modified in before/after).
    restored = [e.path for e in diff if e.kind in ("created", "modified")]
    removed = [e.path for e in diff if e.kind == "deleted"]

    # Record the reversal in the attest log for a complete audit trail.
    revert_id = change_id_now()
    attest.append_event(
        _log_path(store),
        "revert",
        {
            "kind": "revert",
            "revert_id": revert_id,
            "reverted_change": change_id,
            "backend": backend.name,
            "root": root_path.as_posix(),
            "restored": restored,
            "removed": removed,
        },
    )

    return RevertResult(
        change_id=change_id,
        backend=backend.name,
        root=root_path.as_posix(),
        ok=True,
        restored=restored,
        removed=removed,
    )
