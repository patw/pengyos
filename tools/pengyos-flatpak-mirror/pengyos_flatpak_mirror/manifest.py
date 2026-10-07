"""Load and validate the declarative ``mirror.toml`` manifest.

This module *defines* the ``mirror.toml`` schema: the
remote to mirror from, the apps to mirror, the pinned commit per ref, and which
collections to pull. It is deliberately dependency-free — parsing uses the
standard-library :mod:`tomllib` (Python 3.11+).

The public surface is intentionally small:

* :class:`ManifestError` — raised for any missing/invalid manifest.
* :class:`App` — a single pinned app ref.
* :class:`MirrorManifest` — the fully validated manifest.
* :func:`load_manifest` — read + validate a ``mirror.toml`` file.

The schema is:

.. code-block:: toml
   :caption: mirror.toml

   # The remote to mirror from (a flatpak/ostree remote name or URL).
   remote = "flathub"

   # Optional local remote name to register; defaults to ``remote``.
   remote-name = "pengyos"

   # Apps to mirror, each pinned to a specific commit.
   apps = [
     { id = "org.example.App", commit = "0a1b2c3d...", collection-id = "org.example" },
   ]

   # Which collection IDs to pull. Omit to derive from ``apps`` (each app's
   # ``collection-id``, manifest order, de-duplicated).
   collections = ["org.example"]
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# A pinned commit is a hex string; require at least this many hex chars so a
# plain typo (or a branch name) is rejected rather than silently mirrored.
MIN_COMMIT_LEN = 8
HEX_COMMIT_RE = re.compile(r"^[0-9a-fA-F]+$")

# Default Flatpak metadata when an app entry omits these keys.
DEFAULT_ARCH = "x86_64"
DEFAULT_BRANCH = "stable"

# Sentinel distinguishing "the key is absent" from "the key is present but
# empty". ``dict.get`` cannot tell ``collections = []`` apart from a missing
# ``collections`` key, and the schema treats them differently: an omitted key
# derives its collections from the apps, while an explicit empty list pulls no
# collections at all.
_UNSET = object()


class ManifestError(Exception):
    """Raised when a mirror manifest is missing, unreadable, or invalid.

    The message always names the offending key/entry so a bad manifest fails
    loudly with an actionable error (spec: "manifest validation errors").
    """


@dataclass(frozen=True)
class App:
    """A single Flatpak app pinned to a specific commit.

    ``ref`` is the OSTree ref this pins: ``{id}/{arch}/{branch}`` — the exact
    token the runner mirrors (see the runner subtask).
    """

    id: str
    commit: str
    arch: str = DEFAULT_ARCH
    branch: str = DEFAULT_BRANCH
    collection_id: str | None = None

    @property
    def ref(self) -> str:
        """The OSTree ref this app pins: ``{id}/{arch}/{branch}``."""
        return f"{self.id}/{self.arch}/{self.branch}"


@dataclass(frozen=True)
class MirrorManifest:
    """A validated ``mirror.toml``.

    Attributes:
        remote: The remote to mirror from.
        remote_name: The local remote name to register (defaults to ``remote``).
        apps: The pinned apps, in manifest order.
        collections: Collection IDs to pull, in manifest order. When the manifest
            omits ``collections`` entirely, these are derived from each app's
            ``collection-id`` (see :func:`load_manifest`); an explicit
            ``collections = []`` yields an empty tuple.
        source: The path the manifest was loaded from.
    """

    remote: str
    apps: tuple[App, ...] = ()
    collections: tuple[str, ...] = ()
    remote_name: str | None = None
    source: Path | None = None

    @property
    def refs(self) -> list[str]:
        """The OSTree refs pinned by this manifest, in order."""
        return [app.ref for app in self.apps]


# --- validation helpers -----------------------------------------------------


def _require_str(value: Any, key: str, *, where: str) -> str:
    """Return ``value`` as a non-empty string or raise :class:`ManifestError`."""
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: {key!r} must be a non-empty string")
    return value


def _validate_app(entry: Any, index: int, *, where: str) -> App:
    """Validate a single ``apps[]`` entry and return an :class:`App`."""
    if not isinstance(entry, dict):
        raise ManifestError(
            f"{where}[{index}]: each app must be a TOML table (got "
            f"{type(entry).__name__})"
        )

    app_id = _require_str(entry.get("id"), "id", where=f"{where}[{index}]")

    commit = entry.get("commit")
    if not isinstance(commit, str) or not HEX_COMMIT_RE.match(commit):
        raise ManifestError(
            f"{where}[{index}]: commit must be a non-empty hex string "
            f"(got {commit!r})"
        )
    if len(commit) < MIN_COMMIT_LEN:
        raise ManifestError(
            f"{where}[{index}]: commit {commit!r} is too short "
            f"(>= {MIN_COMMIT_LEN} hex chars)"
        )

    arch = entry.get("arch", DEFAULT_ARCH)
    if not isinstance(arch, str) or not arch.strip():
        raise ManifestError(f"{where}[{index}]: arch must be a non-empty string")

    branch = entry.get("branch", DEFAULT_BRANCH)
    if not isinstance(branch, str) or not branch.strip():
        raise ManifestError(f"{where}[{index}]: branch must be a non-empty string")

    collection_id = entry.get("collection-id")
    if collection_id is not None:
        collection_id = _require_str(
            collection_id, "collection-id", where=f"{where}[{index}]"
        )

    return App(
        id=app_id,
        commit=commit,
        arch=arch,
        branch=branch,
        collection_id=collection_id,
    )


def _validate_collection(value: Any, index: int, *, where: str) -> str:
    """Validate a single ``collections[]`` entry and return the string."""
    return _require_str(value, str(index), where=f"{where}[{index}]")


def _derive_collections(apps: tuple[App, ...]) -> tuple[str, ...]:
    """Derive the collection IDs to pull from each app's ``collection-id``.

    Walks ``apps`` in manifest order, collecting each non-``None``
    ``collection_id`` once — first appearance wins, later duplicates are dropped.
    Apps without a ``collection-id`` contribute nothing. This implements the
    "omit to derive from apps" rule: when ``collections`` is absent from the
    manifest, the collections to pull are inferred from the apps being mirrored.
    The result is deterministic (manifest order, stable de-duplication), so the
    sync flow stays idempotent.
    """
    seen: set[str] = set()
    derived: list[str] = []
    for app in apps:
        collection_id = app.collection_id
        if collection_id is None or collection_id in seen:
            continue
        seen.add(collection_id)
        derived.append(collection_id)
    return tuple(derived)


# --- public loader ----------------------------------------------------------


def load_manifest(path: str | Path) -> MirrorManifest:
    """Read, parse, and validate a ``mirror.toml`` manifest.

    Args:
        path: Path to the manifest file.

    Returns:
        A validated :class:`MirrorManifest`.

    Raises:
        ManifestError: If the file is missing/unreadable, is not valid TOML,
            or fails validation.
    """
    path = Path(path)
    if not path.is_file():
        raise ManifestError(f"manifest file not found: {path}")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: invalid TOML: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError(f"{path}: top-level value must be a table")

    remote = _require_str(data.get("remote"), "remote", where=str(path))

    remote_name = data.get("remote-name")
    if remote_name is not None:
        remote_name = _require_str(
            remote_name, "remote-name", where=str(path)
        )

    apps_raw = data.get("apps", [])
    if not isinstance(apps_raw, list):
        raise ManifestError(f"{path}: apps must be a list of tables")
    apps = tuple(
        _validate_app(apps_raw[i], i, where="apps") for i in range(len(apps_raw))
    )

    collections_raw = data.get("collections", _UNSET)
    if collections_raw is _UNSET:
        # `collections` omitted entirely: derive the collection IDs to pull from
        # each app's ``collection-id`` (manifest order, de-duplicated). This is
        # the "omit to derive from apps" rule in the manifest schema.
        collections = _derive_collections(apps)
    else:
        # Present (even as an explicit empty list): use it verbatim. An explicit
        # ``collections = []`` pulls no collections — it is not "omitted".
        if not isinstance(collections_raw, list):
            raise ManifestError(f"{path}: collections must be a list of strings")
        collections = tuple(
            _validate_collection(c, i, where="collections")
            for i, c in enumerate(collections_raw)
        )

    return MirrorManifest(
        remote=remote,
        remote_name=remote_name or remote,
        apps=apps,
        collections=collections,
        source=path,
    )
