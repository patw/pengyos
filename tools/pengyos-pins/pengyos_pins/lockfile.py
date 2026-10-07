"""The resolved-component lockfile and ``drift`` comparison.

The ``lock`` and ``drift`` CLI subcommands (see ``pengyos_pins.cli``) revolve
around a **lockfile** — ``pins.lock.toml`` — a committed snapshot of the
*resolved* components (version + sha256, plus the full component record).  It is
the baseline that ``drift`` compares the working ``pins.toml`` against:

* editing ``pins.toml`` (bump a version, point at a new tarball, add a
  component, drop one) is exactly the *drift* the lockfile detects, and
* ``pengyos-pins lock`` rewrites the lockfile from a validated manifest,
  "committing" the current state as the new baseline.

This module owns three concerns:

* **serialisation** — ``serialize_pins`` (in ``pengyos_pins.schema``) turns a
  :class:`~pengyos_pins.schema.Pins` into a deterministic ``pins.lock.toml``
  document; :func:`load_lockfile` reads one back;
* **resolution** — :func:`write_lockfile` writes a validated manifest to disk as
  the lockfile; and
* **drift** — :func:`drift` diffs a working :class:`Pins` against its locked
  :class:`Pins` and reports added / removed / version-changed / hash-changed
  entries; :func:`format_drift` renders that to a human report.

All operations are pure and network-free; the only side effect is
:func:`write_lockfile` writing a file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .schema import (
    Component,
    Pins,
    PinsError,
    component_table,
    load_pins,
    parse_pins,
    serialize_pins,
)

#: The default lockfile file name.
DEFAULT_LOCKFILE = "pins.lock.toml"

#: Drift categories.  A component that changes *both* version and hash is
#: reported once as a version change (the version bump is the salient fact).
DRIFT_ADDED = "added"
DRIFT_REMOVED = "removed"
DRIFT_VERSION_CHANGED = "version-changed"
DRIFT_HASH_CHANGED = "hash-changed"

#: All drift categories, in the order they are reported.
ALL_DRIFT_CATEGORIES: tuple[str, ...] = (
    DRIFT_ADDED,
    DRIFT_REMOVED,
    DRIFT_VERSION_CHANGED,
    DRIFT_HASH_CHANGED,
)


# --------------------------------------------------------------------------- #
# Lockfile serialisation
# --------------------------------------------------------------------------- #


def serialize_lockfile(pins: Pins, *, header: str | None = None) -> str:
    """Serialise ``pins`` to a ``pins.lock.toml`` document (see
    :func:`pengyos_pins.schema.serialize_pins`)."""
    return serialize_pins(pins, header=header)


def load_lockfile(path: Path | str) -> Pins:
    """Read and parse a ``pins.lock.toml`` lockfile at ``path``.

    The lockfile uses the same ``[[component]]`` array-of-tables shape as
    ``pins.toml`` and is validated by the same schema loader, so a lockfile is
    itself a fully valid :class:`Pins` document.  Raises
    :class:`PinsError` (missing file / malformed TOML / schema failure) — a
    lockfile that fails validation is a hard error, never a silent drift.
    """
    return load_pins(path)


def write_lockfile(
    pins: Pins,
    path: Path | str = DEFAULT_LOCKFILE,
    *,
    header: str | None = None,
) -> str:
    """Write ``pins`` to ``path`` as a resolved lockfile.

    Returns the path written (as a string).  The document is deterministic
    (name-sorted) so re-locking an unchanged manifest is byte-stable.
    """
    path = Path(path)
    path.write_text(serialize_lockfile(pins, header=header), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DriftEntry:
    """One detected difference between the working pins and the lockfile.

    ``category`` is one of the :data:`ALL_DRIFT_CATEGORIES` values.  For a
    version/hash change the entry carries both the locked and working values so
    a caller can show the diff; for an add/remove it carries only the value that
    exists on the relevant side.
    """

    category: str
    name: str
    locked_version: str = ""
    working_version: str = ""
    locked_sha256: str = ""
    working_sha256: str = ""


def _entries_by_name(pins: Pins) -> dict[str, Component]:
    return {component.name: component for component in pins.components}


def drift(pins: Pins, locked: Pins) -> list[DriftEntry]:
    """Diff the working ``pins`` against the locked ``locked`` manifest.

    Returns the detected drift in a deterministic order (added first by name,
    then removed, then version-changed, then hash-changed).  A component that
    changed version is reported once as a version change even if its hash also
    changed; a component whose version is unchanged but whose hash changed is
    reported as a hash change.  No component appears in more than one entry.
    """
    working = _entries_by_name(pins)
    resolved = _entries_by_name(locked)

    added: list[DriftEntry] = []
    removed: list[DriftEntry] = []
    version_changed: list[DriftEntry] = []
    hash_changed: list[DriftEntry] = []

    # Added: present in the working set, absent from the lock.
    for name in sorted(working):
        if name not in resolved:
            added.append(
                DriftEntry(DRIFT_ADDED, name, working_version=working[name].version)
            )

    # Removed: present in the lock, absent from the working set.
    for name in sorted(resolved):
        if name not in working:
            removed.append(
                DriftEntry(DRIFT_REMOVED, name, locked_version=resolved[name].version)
            )

    # Present on both sides: compare version, then hash.
    for name in sorted(set(working) & set(resolved)):
        w = working[name]
        r = resolved[name]
        if w.version != r.version:
            version_changed.append(
                DriftEntry(
                    DRIFT_VERSION_CHANGED,
                    name,
                    locked_version=r.version,
                    working_version=w.version,
                    locked_sha256=r.sha256,
                    working_sha256=w.sha256,
                )
            )
        elif w.sha256 != r.sha256:
            hash_changed.append(
                DriftEntry(
                    DRIFT_HASH_CHANGED,
                    name,
                    locked_version=r.version,
                    working_version=w.version,
                    locked_sha256=r.sha256,
                    working_sha256=w.sha256,
                )
            )

    return [*added, *removed, *version_changed, *hash_changed]


def _format_entry(entry: DriftEntry) -> str:
    """Render a single :class:`DriftEntry` as one report line."""
    if entry.category == DRIFT_ADDED:
        return f"added:      {entry.name} ({entry.working_version})"
    if entry.category == DRIFT_REMOVED:
        return f"removed:    {entry.name} ({entry.locked_version})"
    if entry.category == DRIFT_VERSION_CHANGED:
        return (
            f"version:    {entry.name} {entry.locked_version} -> {entry.working_version}"
        )
    if entry.category == DRIFT_HASH_CHANGED:
        return (
            f"hash:       {entry.name} "
            f"{entry.locked_sha256[:12]}.. -> {entry.working_sha256[:12]}.."
        )
    raise ValueError(f"unknown drift category: {entry.category!r}")


def format_drift(entries: Sequence[DriftEntry], *, source: str = "pins.toml",
                 lockfile: str = DEFAULT_LOCKFILE) -> str:
    """Render drift entries as a human-readable report.

    A short header summarises the counts per category, then one line per entry.
    An empty diff prints a single "no drift" line.
    """
    if not entries:
        return "no drift: pins.toml matches the lockfile"
    counts: dict[str, int] = {category: 0 for category in ALL_DRIFT_CATEGORIES}
    for entry in entries:
        counts[entry.category] += 1
    lines = [
        f"drift detected between {source} and {lockfile}:",
        "",
        f"  added:      {counts[DRIFT_ADDED]}",
        f"  removed:    {counts[DRIFT_REMOVED]}",
        f"  version:    {counts[DRIFT_VERSION_CHANGED]}",
        f"  hash:       {counts[DRIFT_HASH_CHANGED]}",
        "",
    ]
    lines.extend(_format_entry(entry) for entry in entries)
    return "\n".join(lines)


def drift_counts(entries: Sequence[DriftEntry]) -> dict[str, int]:
    """Return a `{category: count}` map for a list of drift entries."""
    counts: dict[str, int] = {category: 0 for category in ALL_DRIFT_CATEGORIES}
    for entry in entries:
        counts[entry.category] += 1
    return counts


# --------------------------------------------------------------------------- #
# Test helper
# --------------------------------------------------------------------------- #


def lockfile_table(
    name: str = "linux",
    *,
    version: str = "7.2.5",
    sha256: str = "0" * 64,
) -> dict:
    """Build a minimal valid ``[[component]]`` table for a lockfile fixture."""
    return component_table(name, version=version, sha256=sha256)
