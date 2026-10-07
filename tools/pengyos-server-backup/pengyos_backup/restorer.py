"""Prove a documented restore reproduces a known tree (US-SRV-4).

``plan`` emits the backup service; this module is its mirror image: it *proves*
that a restore from a repository reproduces a known tree. A restore is only as
good as a test that proves it, so ``verify-restore``:

1. reads a **fixture repository** (a committed, restic-like repo — see
   :data:`FIXTURE_REPO`) that mimics restic's content-addressed layout
   (``content/<sha256>`` blobs + a ``snapshot.json`` manifest),
2. runs a **restore** of a snapshot from that repo into a target directory via
   a :class:`Runner` (mockable — see below),
3. computes the **tree hash** of the restored tree and asserts it equals the
   snapshot's golden ``tree_hash``.

If the restore fails, or the restored tree does not hash to the golden value,
the proof fails loudly (a :class:`RestoreError` / :class:`VerifyError`) — the
restore proof has teeth.

Mockable runner
---------------
Everything that touches the outside world goes through a :class:`Runner`.
Production uses :class:`SubprocessRunner` (runs a real ``restic``/``rustic
restore``); :class:`SimulatedRunner` performs the offline simulation over the
committed fixture repo (so ``verify-restore`` works with no restic installed);
and tests swap in :class:`FakeRunner` to assert on the exact restore argv and to
script pass/fail outcomes. This mirrors the pattern in the sibling
``pengyos-guest`` tool.

Repository format
-----------------
The fixture repo is a deliberately faithful *subset* of a restic repo:

``config``
    An empty marker file — every restic repo has one.

``snapshot.json``
    The snapshot metadata + manifest. ``entries`` lists the restored tree as
    ``{path, size, hash}`` (content hash); ``tree_hash`` is the golden value the
    restore must reproduce; ``id`` is the snapshot id.

``content/<sha256>``
    One blob per file, keyed by its content hash.

The golden ``tree_hash`` is computed over the manifest entries (sorted by path,
``path<TAB>size<TAB>hash`` joined by newlines, sha256) — the same path+size+
content digest restic-style tooling would compute over the restored tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .errors import PengyOSBackupError, RepoError

# --- paths -------------------------------------------------------------------

#: The committed fixture repository — a restic-like repo used by the offline
#: simulation. Relative to the repo root (parent of ``pengyos_backup/``).
FIXTURE_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "restic-repo"

#: The snapshot id the fixture repo ships with.
FIXTURE_SNAPSHOT_ID = "snap-20260919023000-e3b0c44298fc1c14"

#: The golden tree hash the fixture snapshot restores to.
FIXTURE_TREE_HASH = "c0c312752290795c0a528e3f718ea1035bf0fd43a52a5a6d1895a9fa4d788408"


# --- tree model --------------------------------------------------------------


@dataclass(frozen=True)
class TreeEntry:
    """A single restored file: its repo-relative path, byte size, content hash."""

    path: str
    size: int
    content_hash: str


def tree_hash(entries: Sequence[TreeEntry]) -> str:
    """The digest of a tree over its entries.

    Each entry contributes ``path<TAB>size<TAB>content_hash``; the entries are
    sorted by path, joined by newlines, and sha256'd. This is the same kind of
    path+size+content digest a restic-style tool computes over the restored
    tree, so the fixture's golden ``tree_hash`` and the restored tree's hash are
    computed by the same function and can be compared directly.
    """

    lines = [
        f"{e.path}\t{e.size}\t{e.content_hash}"
        for e in sorted(entries, key=lambda e: e.path)
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def scan_tree(target: Path) -> list[TreeEntry]:
    """Scan *target*, returning one :class:`TreeEntry` per regular file.

    Paths are repo-relative (the path under *target*). Symlinks and directories
    are ignored — only regular files make up the restored tree.
    """

    entries: list[TreeEntry] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(target).as_posix()
        data = path.read_bytes()
        entries.append(
            TreeEntry(
                path=rel,
                size=len(data),
                content_hash=hashlib.sha256(data).hexdigest(),
            )
        )
    return entries


# --- snapshot / repo ---------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """A snapshot: its id, the golden tree hash it restores to, and its manifest."""

    id: str
    tree_hash: str
    entries: tuple[TreeEntry, ...]
    host: str | None = None
    paths: tuple[str, ...] = ()


def _load_snapshot_json(repo: Path) -> dict:
    """Read and parse ``repo/snapshot.json``.

    Raises :class:`RepoError` if the file is missing or not a valid fixture.
    """

    meta = repo / "snapshot.json"
    if not meta.is_file():
        raise RepoError(f"not a restic-like repo (no {meta.name}): {repo}")
    try:
        doc = json.loads(meta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RepoError(f"invalid {meta.name}: {exc}") from exc

    if doc.get("format") != "pengyos-backup-fixture/v1":
        raise RepoError(
            f"{meta.name} is not a pengyos-backup fixture "
            f"(expected format 'pengyos-backup-fixture/v1')"
        )
    return doc


def load_snapshot(repo: os.PathLike[str] | str) -> Snapshot:
    """Load the fixture snapshot from *repo* (its ``snapshot.json``).

    The fixture repo ships exactly one snapshot; this loads it. Raises
    :class:`RepoError` if the repo is not a valid fixture repo.
    """

    doc = _load_snapshot_json(Path(repo))
    entries = tuple(
        TreeEntry(
            path=e["path"],
            size=int(e["size"]),
            content_hash=e["hash"],
        )
        for e in doc["entries"]
    )
    return Snapshot(
        id=doc["id"],
        tree_hash=doc["tree_hash"],
        entries=entries,
        host=doc.get("host"),
        paths=tuple(doc.get("paths", ())),
    )


# RepoError lives in pengyos_backup.errors (the error hierarchy); it is imported
# there and re-exported via this module's __all__ for caller convenience.


# --- runner ------------------------------------------------------------------


@dataclass
class RunResult:
    """The outcome of a single restore invocation."""

    argv: list[str]
    returncode: int
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """True when the restore command exited cleanly."""

        return self.returncode == 0


class Runner:
    """Interface for executing a restic/rustic restore.

    Subclasses implement :meth:`restore`. Production uses
    :class:`SubprocessRunner`; tests swap in :class:`FakeRunner`. The argv that
    would be executed — ``<binary> restore <snapshot> --repo <repo> --target
    <target>`` — is the contract callers assert on.
    """

    def restore(
        self,
        *,
        binary: str,
        snapshot_id: str,
        repo: str,
        target: str,
    ) -> RunResult:  # pragma: no cover - interface
        raise NotImplementedError


def restore_argv(
    binary: str, snapshot_id: str, repo: str, target: str
) -> list[str]:
    """The golden ``<binary> restore`` argv.

    ``restic restore <snapshot> --repo <repo> --target <target>``. This is the
    single source of truth for the restore argv, shared by the runner and the
    tests, so the simulated and real restores can never diverge.
    """

    return [binary, "restore", snapshot_id, "--repo", repo, "--target", target]


class SubprocessRunner(Runner):
    """Run a real ``restic``/``rustic restore`` via :func:`subprocess.run`.

    This is the production path (used only when a real backend is available —
    integration tests behind ``PENGYOS_IT=1``). A missing binary is a clean,
    non-zero result, not a crash, so the CLI can report it as a normal failure.
    """

    def restore(
        self,
        *,
        binary: str,
        snapshot_id: str,
        repo: str,
        target: str,
    ) -> RunResult:  # pragma: no cover - needs a real backend
        argv = restore_argv(binary, snapshot_id, repo, target)
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            return RunResult(
                argv=argv,
                returncode=127,
                stderr=f"{binary!r} not found: install it and put it on PATH",
            )
        return RunResult(
            argv=argv,
            returncode=proc.returncode,
            stderr=proc.stderr,
        )


class SimulatedRunner(Runner):
    """Offline simulation of a restore over a committed fixture repo.

    This is the default runner: it needs no restic/rustic installed, no network,
    and no root. It reads the fixture repo's ``snapshot.json`` manifest and
    writes each blob (``content/<hash>``) to ``<target>/<path>``, reproducing the
    snapshot's tree. A missing blob is a clean restore failure (this is what
    gives the restore proof its teeth — see the follow-up task).
    """

    def restore(
        self,
        *,
        binary: str,
        snapshot_id: str,
        repo: str,
        target: str,
    ) -> RunResult:
        argv = restore_argv(binary, snapshot_id, repo, target)
        snap = load_snapshot(repo)
        if snap.id != snapshot_id:
            return RunResult(
                argv=argv,
                returncode=1,
                stderr=f"snapshot {snapshot_id!r} not found in {repo}",
            )

        blobs = {e.content_hash: e.path for e in snap.entries}
        out_dir = Path(target)
        out_dir.mkdir(parents=True, exist_ok=True)
        missing: list[str] = []
        for entry in snap.entries:
            blob = Path(repo) / "content" / entry.content_hash
            if not blob.is_file():
                missing.append(entry.path)
                continue
            dest = out_dir / entry.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob.read_bytes())

        if missing:
            return RunResult(
                argv=argv,
                returncode=1,
                stderr=(
                    f"restore failed: {len(missing)} missing blob(s) in {repo}: "
                    + ", ".join(missing)
                ),
            )
        return RunResult(argv=argv, returncode=0)


@dataclass
class FakeRunner(Runner):
    """In-memory runner for tests.

    Records every restore call in :attr:`calls` and returns results from a
    scripted :attr:`script` (matched by argv prefix) or a default result. Used
    to assert on the exact restore argv and to script pass/fail outcomes without
    touching a real repo.
    """

    calls: list[list[str]] = field(default_factory=list)
    script: list[tuple[list[str], RunResult]] = field(default_factory=list)
    default: RunResult = field(
        default_factory=lambda: RunResult(argv=[], returncode=0)
    )

    def respond(
        self, argv_prefix: Sequence[str], result: RunResult
    ) -> None:
        """Queue *result* for any restore whose argv starts with *argv_prefix*."""

        self.script.append((list(argv_prefix), result))

    def restore(
        self,
        *,
        binary: str,
        snapshot_id: str,
        repo: str,
        target: str,
    ) -> RunResult:
        argv = restore_argv(binary, snapshot_id, repo, target)
        self.calls.append(argv)
        for prefix, result in self.script:
            if argv[: len(prefix)] == prefix:
                return result
        return self.default


# --- verify ------------------------------------------------------------------


@dataclass
class VerifyResult:
    """The outcome of :func:`verify_restore`.

    ``ok`` is True only when the restore succeeded *and* the restored tree hashes
    to the snapshot's golden ``tree_hash``. ``restored`` lists the files written;
    ``error`` explains the failure (restore error or hash mismatch) when not ok.
    """

    ok: bool
    tree_hash: str
    expected: str
    restored: list[str] = field(default_factory=list)
    error: str | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.ok:
            return (
                f"restore verified: {len(self.restored)} file(s), "
                f"tree hash {self.tree_hash}"
            )
        return f"restore verification failed: {self.error}"


def verify_restore(
    repo: os.PathLike[str] | str,
    target: os.PathLike[str] | str,
    *,
    snapshot_id: str = FIXTURE_SNAPSHOT_ID,
    runner: Runner | None = None,
    binary: str = "restic",
) -> VerifyResult:
    """Restore *snapshot_id* from *repo* into *target* and assert its tree hash.

    Runs the restore via *runner* (default :class:`SimulatedRunner`), then
    computes the restored tree's hash and compares it to the snapshot's golden
    ``tree_hash``. Returns a :class:`VerifyResult`; ``ok`` is False (with an
    ``error``) for *every* failure mode — a corrupt/missing/invalid fixture
    repo, a missing snapshot, a failed restore, or a tree-hash mismatch — so the
    restore proof's teeth are uniform: callers get a ``VerifyResult`` to inspect,
    never an exception.
    """

    try:
        snap = load_snapshot(repo)
    except RepoError as exc:
        # A corrupt/missing/invalid fixture repo is a clean proof failure: the
        # restore cannot be proven, so surface it as ``ok=False`` rather than
        # raising. (The CLI already handles a raised ``RepoError`` too, but the
        # proof should fail uniformly via its return value.)
        return VerifyResult(
            ok=False,
            tree_hash="",
            expected="",
            error=str(exc),
        )
    if snapshot_id != snap.id:
        return VerifyResult(
            ok=False,
            tree_hash="",
            expected=snap.tree_hash,
            error=f"snapshot {snapshot_id!r} not found in {repo}",
        )

    runner = runner if runner is not None else SimulatedRunner()
    result = runner.restore(
        binary=binary,
        snapshot_id=snapshot_id,
        repo=str(repo),
        target=str(target),
    )
    if not result.ok:
        return VerifyResult(
            ok=False,
            tree_hash="",
            expected=snap.tree_hash,
            error=result.stderr.strip() or f"exit code {result.returncode}",
        )

    entries = scan_tree(Path(target))
    restored = [e.path for e in entries]
    computed = tree_hash(entries)
    if computed != snap.tree_hash:
        return VerifyResult(
            ok=False,
            tree_hash=computed,
            expected=snap.tree_hash,
            restored=restored,
            error=(
                f"restored tree hash {computed} != expected "
                f"{snap.tree_hash}"
            ),
        )
    return VerifyResult(
        ok=True,
        tree_hash=computed,
        expected=snap.tree_hash,
        restored=restored,
    )


__all__ = [
    "FIXTURE_REPO",
    "FIXTURE_SNAPSHOT_ID",
    "FIXTURE_TREE_HASH",
    "RunResult",
    "Runner",
    "SubprocessRunner",
    "SimulatedRunner",
    "FakeRunner",
    "Snapshot",
    "TreeEntry",
    "RepoError",
    "VerifyResult",
    "load_snapshot",
    "restore_argv",
    "scan_tree",
    "tree_hash",
    "verify_restore",
]
