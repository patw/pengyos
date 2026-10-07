"""``apply`` — snapshot → run command → record → capture diff.

The heart of the mutation loop. :func:`apply_change`

1. snapshots the tracked tree (via a :class:`~pengyos_mutations.backends.Backend`),
2. runs the caller-supplied command under that snapshot,
3. records the attempt in the attest log — via the canonical ``pengyos-attest``
   log when it is importable, else this package's local hash-chained chain —
4. and captures the diff of tracked paths (created / modified / deleted).

The command runs with its working directory at the tracked root by default so
that it naturally mutates the tracked tree; override with ``--cwd``. A failing
command is still recorded (its exit code is captured) and revert remains
available, so a botched live fix can always be rolled back.

The tracked set is a single directory (``--root``, default: the current working
directory). The backend snapshots the whole directory (so :mod:`.revert` can
restore it byte-for-byte); the diff is computed at file granularity by walking
the tree before and after the command.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from pengyos_mutations.backends import BackendError, get_backend

__all__ = [
    "ApplyError",
    "ApplyResult",
    "DiffEntry",
    "apply_change",
    "change_id_now",
    "default_store",
    "iter_tree",
    "walk_diff",
]

#: Environment override for the mutations store directory.
STORE_ENV = "PENGYOS_MUTATIONS_STORE"
#: Environment override for the tracked root.
ROOT_ENV = "PENGYOS_MUTATIONS_ROOT"

#: The stable key under which the tracked root is recorded in a snapshot.
ROOT_KEY = "root"


class ApplyError(RuntimeError):
    """Raised when an application cannot be completed."""


@dataclass
class DiffEntry:
    """One path in the diff between the pre- and post-command tree.

    Attributes
    ----------
    path:
        The path relative to the tracked root.
    kind:
        One of ``"created"``, ``"modified"``, or ``"deleted"``.
    """

    path: str
    kind: str


@dataclass
class ApplyResult:
    """Outcome of :func:`apply_change`.

    Attributes
    ----------
    change_id:
        The stable identifier of the recorded change.
    exit_code:
        The command's exit code (``None`` when the command could not be run).
    diff:
        The tracked-path diff (created / modified / deleted).
    log_path:
        The attest log the event was written to.
    record:
        The attest record that was appended.
    backend:
        The name of the backend that produced the snapshot.
    """

    change_id: str
    exit_code: Optional[int]
    diff: list[DiffEntry]
    log_path: Path
    record: dict
    backend: str


def default_store() -> Path:
    """The default mutations store directory.

    Honours ``$PENGYOS_MUTATIONS_STORE`` when set, otherwise
    ``~/.config/pengyos/mutations``.
    """
    override = os.environ.get(STORE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "pengyos" / "mutations"


def change_id_now() -> str:
    """A monotonic-ish, collision-resistant change id: ``<UTC ts>-<uuid4 short>``.

    The timestamp prefix keeps ids sortable in the log; the short uuid4 tail
    disambiguates changes applied within the same second.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{uuid.uuid4().hex[:8]}"


def iter_tree(root: Path):
    """Yield ``(relative_posix_path, absolute_path)`` for every file under ``root``.

    Symlinks are followed to their target so the diff reflects real content;
    directories, special files, and unreadable entries are skipped.
    """
    root = Path(root)
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames.sort()
        for name in sorted(filenames):
            abs_path = Path(dirpath) / name
            try:
                if abs_path.is_symlink() and not abs_path.exists():
                    # Broken symlink — skip rather than crash the walk.
                    continue
                yield abs_path.relative_to(root).as_posix(), abs_path
            except (OSError, ValueError):
                continue


def _file_digest(path: Path) -> str:
    """A content digest for a file (``sha256`` of its bytes)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_diff(root: Path, before: dict, after: dict) -> list[DiffEntry]:
    """Diff two ``{relpath: digest}`` maps into :class:`DiffEntry` records.

    Keys present only in ``after`` are ``created``; keys only in ``before`` are
    ``deleted``; keys in both whose digest differs are ``modified``. Results are
    sorted by path for a stable, deterministic ordering.
    """
    entries: list[DiffEntry] = []
    for relpath in set(before) | set(after):
        if relpath not in before:
            entries.append(DiffEntry(path=relpath, kind="created"))
        elif relpath not in after:
            entries.append(DiffEntry(path=relpath, kind="deleted"))
        elif before[relpath] != after[relpath]:
            entries.append(DiffEntry(path=relpath, kind="modified"))
    entries.sort(key=lambda e: e.path)
    return entries


def _run_command(command: Sequence[str], cwd: Optional[Path]) -> Optional[int]:
    """Run ``command`` (argv list) with ``cwd``; return its exit code.

    Returns ``None`` when the command could not be launched (e.g. not found).
    """
    if not command:
        return None
    try:
        proc = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return None
    return proc.returncode


def _record_event(
    log_path: Path, change_id: str, record_data: dict
) -> dict:
    """Append the apply event to the attest log.

    Prefers the canonical ``pengyos-attest`` log when that package is
    importable (its records are schema/hash-compatible with the local chain),
    and otherwise falls back to this package's local hash-chained chain.
    """
    event_type = "apply"
    try:
        from pengyos_attest import chain as external_chain  # type: ignore
    except ModuleNotFoundError:
        from pengyos_mutations import attest as local_chain
        return local_chain.append_event(log_path, event_type, record_data)
    return external_chain.append_event(log_path, event_type, record_data)


def apply_change(
    desc: str,
    store: Optional[str] = None,
    command: Optional[Sequence[str]] = None,
    *,
    root: Optional[str] = None,
    cwd: Optional[str] = None,
    backend: str = "mock",
) -> ApplyResult:
    """Snapshot ``root``, run ``command``, record the change, and capture its diff.

    Parameters
    ----------
    desc:
        Human-readable description of the change (stored in the attest record).
    store:
        Mutations store directory. Defaults to ``$PENGYOS_MUTATIONS_STORE`` or
        ``~/.config/pengyos/mutations``.
    command:
        The argv to run under the snapshot. ``None``/empty runs nothing.
    root:
        The directory to track (snapshot + diff). Defaults to ``$PENGYOS_MUTATIONS_ROOT``
        or the current working directory.
    cwd:
        Working directory for ``command``. Defaults to ``root``.
    backend:
        Backend name (see :func:`pengyos_mutations.backends.get_backend`).

    Raises
    ------
    ApplyError
        If the store cannot be prepared, the backend fails, or the tracked root
        does not exist.
    BackendError
        Propagated from the backend (e.g. an unknown backend name).
    """
    command = list(command or [])
    store_path = Path(store).expanduser() if store else default_store()
    store_path.mkdir(parents=True, exist_ok=True)

    if root:
        root_path = Path(root).expanduser()
    elif os.environ.get(ROOT_ENV):
        root_path = Path(os.environ[ROOT_ENV]).expanduser()
    else:
        root_path = Path.cwd()
    root_path = Path(root_path).resolve()
    if not root_path.is_dir():
        raise ApplyError(f"tracked root is not a directory: {root_path}")

    # The store must live OUTSIDE the tracked root: restoring the root on
    # revert would otherwise clobber the snapshot store (and vice versa).
    store_resolved = store_path.resolve()
    if _is_within(store_resolved, root_path):
        raise ApplyError(
            f"the mutations store {store_resolved} must not be inside the "
            f"tracked root {root_path}; pick a store outside the tracked tree"
        )

    change = change_id_now()
    tracked = {ROOT_KEY: root_path}

    backend_instance = get_backend(backend, store_path)

    # 1. Snapshot the tracked tree so a later revert can restore it.
    try:
        backend_instance.snapshot(change, tracked)
    except BackendError:
        raise
    except Exception as exc:  # backend filesystem failure -> still surface cleanly
        raise ApplyError(f"snapshot failed: {exc}") from exc

    # 2. Capture the pre-command tree for the diff.
    before = {rel: _file_digest(abs_path) for rel, abs_path in iter_tree(root_path)}

    # 3. Run the command under the snapshot.
    run_cwd = Path(cwd).expanduser() if cwd else root_path
    exit_code = _run_command(command, run_cwd)

    # 4. Capture the post-command tree and diff it against the pre-command state.
    after = {rel: _file_digest(abs_path) for rel, abs_path in iter_tree(root_path)}
    diff = walk_diff(root_path, before, after)

    # 5. Record the attempt in the attest log (failing commands included).
    log_path = store_path / "events.jsonl"
    record_data: dict[str, Any] = {
        "kind": "apply",
        "change_id": change,
        "desc": desc,
        "backend": backend_instance.name,
        "root": root_path.as_posix(),
        "command": [str(c) for c in command],
        "exit_code": exit_code,
        "diff": [{"path": e.path, "kind": e.kind} for e in diff],
    }
    record = _record_event(log_path, change, record_data)

    return ApplyResult(
        change_id=change,
        exit_code=exit_code,
        diff=diff,
        log_path=log_path,
        record=record,
        backend=backend_instance.name,
    )


def _is_within(child: Path, parent: Path) -> bool:
    """True when ``child`` is ``parent`` or lives under it (both must be resolved)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
