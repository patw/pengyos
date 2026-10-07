"""Higher-level orchestration: tie the declarative manifest to the runner.

The runner (:mod:`pengyos_flatpak_mirror.runner`) owns the low-level, mockable
``flatpak``/``ostree`` operations (add remote, mirror a ref, list refs, verify,
prune). This module implements the *flow* that the CLI subcommands run — the
sync sequence (register the remote once, then mirror every pinned ref), the
``ostree refs`` output parser, and the structured list report — so the CLI can
load a manifest, hand a :class:`Runner` to these functions, and format the
result.

Keeping the orchestration here (rather than in ``cli.py``) makes each flow
unit-testable directly against a fake :class:`Runner` — no argparse, no real
flatpak/ostree, network, or root.

The public surface:

* :class:`SyncResult` — what :func:`sync` reported.
* :func:`sync` — the sync flow (add remote, mirror each app).
* :func:`sync_from_manifest` — convenience: load a manifest, then :func:`sync`.
* :func:`parse_refs` — parse ``ostree refs`` stdout into ``(ref, commit)`` pairs.
* :class:`ListResult` + :func:`list_report` — a structured view of that output.
* :func:`list_report_dict` — the deterministic machine report (JSON-ready dict).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .manifest import MirrorManifest, load_manifest
from .runner import Runner

__all__ = [
    "SyncResult",
    "ListResult",
    "sync",
    "sync_from_manifest",
    "parse_refs",
    "list_report",
    "list_report_dict",
]


@dataclass(frozen=True)
class SyncResult:
    """The outcome of a :func:`sync` run.

    Attributes:
        remote_name: The local remote name that was registered.
        remote: The remote URL/name that was registered.
        refs_synced: The OSTree refs mirrored, in manifest order.
        collections_pulled: The collection IDs pulled, in manifest order.
    """

    remote_name: str
    remote: str
    refs_synced: tuple[str, ...]
    collections_pulled: tuple[str, ...] = ()


def sync(manifest: MirrorManifest, runner: Runner, *,
         logger: Callable[[str], None] | None = None) -> SyncResult:
    """Mirror the pinned refs described by ``manifest``.

    The flow is deterministic and idempotent:

    1. Register the upstream remote once via ``flatpak remote-add
       --if-not-exists`` (a no-op if it already exists).
    2. For each app in manifest order, mirror its pinned ref at its exact
       commit via ``flatpak update --user --noninteractive --commit=<commit>
       <id>@<arch>/<branch>``.
    3. For each collection in ``manifest.collections`` order, pull the whole
       collection via ``flatpak update --user --noninteractive
       --collection=<collection-id> <remote-name>``.

    Re-running issues the same argv sequence (the golden argv is pinned to the
    remote name, the exact commit, and the collection ids), so a second run makes
    no *changes* — this is the idempotency the spec tests against a mocked runner.

    Args:
        manifest: The validated manifest to sync from.
        runner: The mockable runner; every external call goes through it.
        logger: Optional ``msg -> None`` for progress lines (runner default is
            a no-op logger).

    Returns:
        A :class:`SyncResult` describing what was synced.
    """
    # Register the remote once, idempotently (--if-not-exists).
    runner.add_remote(manifest.remote_name, manifest.remote)
    if logger is not None:
        logger(f"remote: registered '{manifest.remote_name}' -> {manifest.remote}")

    refs_synced: list[str] = []
    for app in manifest.apps:
        runner.mirror_ref(app.id, app.arch, app.branch, app.commit)
        refs_synced.append(app.ref)
        if logger is not None:
            logger(f"sync: mirrored {app.ref} @ {app.commit}")

    collections_pulled: list[str] = []
    for collection_id in manifest.collections:
        runner.pull_collection(collection_id, manifest.remote_name)
        collections_pulled.append(collection_id)
        if logger is not None:
            logger(f"sync: pulled collection {collection_id}")

    return SyncResult(
        remote_name=manifest.remote_name,
        remote=manifest.remote,
        refs_synced=tuple(refs_synced),
        collections_pulled=tuple(collections_pulled),
    )


def sync_from_manifest(path: str | Path, runner: Runner, *,
                       logger: Callable[[str], None] | None = None) -> SyncResult:
    """Load a manifest from ``path`` then :func:`sync` it.

    Convenience wrapper for the CLI: it raises :class:`ManifestError` (which the
    CLI turns into a clean exit 1) if the manifest is missing/invalid, before
    any external command runs.
    """
    manifest = load_manifest(Path(path))
    return sync(manifest, runner, logger=logger)


def parse_refs(stdout: str) -> list[tuple[str, str]]:
    """Parse ``ostree refs`` stdout into ``(ref, commit)`` pairs.

    ``ostree refs`` prints one line per ref as ``<ref><whitespace><commit>``
    (tab-separated in practice); refs never contain whitespace, so splitting on
    the last run of whitespace yields exactly ``(ref, commit)``. Blank lines and
    ``#`` comments are ignored, and any line that does not look like
    ``<ref> <commit>`` is skipped rather than raising — a partial/corrupt dump
    degrades gracefully instead of crashing the CLI.

    Input order is preserved (no sorting): determinism of the *rendered* report
    is a later concern; this only guarantees a faithful, lossless parse.
    """
    refs: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        refs.append((parts[0], parts[1]))
    return refs


@dataclass(frozen=True)
class ListResult:
    """A structured view of ``ostree refs`` output.

    Attributes:
        refs: The ``(ref, commit)`` pairs, in the order ``ostree refs`` printed
            them.
    """

    refs: tuple[tuple[str, str], ...]


def list_report(stdout: str) -> ListResult:
    """Wrap :func:`parse_refs` in a :class:`ListResult`."""
    return ListResult(tuple(parse_refs(stdout)))


def list_report_dict(stdout: str, repo: str | None = None) -> dict:
    """Build the deterministic machine report for ``ostree refs`` output.

    This is the JSON-ready twin of :func:`parse_refs` / :func:`list_report`.
    Where the human ``list`` preserves the order ``ostree refs`` happened to
    print (lossless, faithful), the machine report is **deterministic**: the
    ``(ref, commit)`` pairs are sorted by ``(ref, commit)`` so the report is
    identical for a given repo regardless of the order the underlying tool
    emitted the refs in. That is the "deterministic" clause of the report
    subtask — two runs against the same repo produce byte-identical output.

    Args:
        stdout: Raw ``ostree refs`` output (parsed the same way as
            :func:`parse_refs` — blanks and ``#`` comments are ignored).
        repo: Optional path the refs came from; included as top-level ``repo``
            when given so the report is self-describing.

    Returns:
        A dict of the form ``{"refs": [{"ref": r, "commit": c}, ...]}`` (plus a
        top-level ``repo`` key when ``repo`` is not ``None``). Keys are
        alphabetical and refs are sorted, so serialising with
        ``json.dumps(..., sort_keys=True)`` is stable.
    """
    refs = sorted(parse_refs(stdout), key=lambda pair: (pair[0], pair[1]))
    report: dict = {"refs": [{"ref": r, "commit": c} for r, c in refs]}
    if repo is not None:
        report["repo"] = repo
    return report
