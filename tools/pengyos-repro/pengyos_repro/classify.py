"""Path classification: bucket every artifact as identical / differs / only-in-A / only-in-B.

It builds on the
:class:`~pengyos_repro.manifest.FileEntry` objects produced by
:func:`~pengyos_repro.manifest.manifest` and answers the question the whole
reproducibility pipeline exists to: *given two build trees, which artifacts are
byte-identical, which differ, and which exist in only one tree?*

Design
------
* **Pure.** :func:`classify` takes two manifest dicts (``{relpath: FileEntry}``)
  and returns a :class:`Diff`. It performs no filesystem I/O, so it is trivially
  unit-testable and deterministic.
* **One bucket per path.** Every relative path that appears in either manifest is
  assigned exactly one of the four statuses. A path cannot be both ``only_in_a``
  and ``differing``, etc.
* **Sorted output.** All four lists are returned in sorted (POSIX) order so the
  report is stable across runs regardless of filesystem walk order.
* **Content, not metadata.** Classification is purely on the streaming ``sha256``
  of each artifact (see manifest.py). Two files with the same digest are
  byte-identical; anything else is a real content difference. Distinguishing
  "metadata-only" changes (timestamps, embedded user, seeds) is the next
  subtask (ignore-patterns), not this one.

The next subtask (report emission) consumes this :class:`Diff` to build the
human report and the ``{score, identical, differing:[{path,reason}],
missing:[...]}`` JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Mapping, Sequence

from .manifest import FileEntry

# The four mutually-exclusive classification buckets.
IDENTICAL = "identical"
DIFFERS = "differs"
ONLY_IN_A = "only-in-A"
ONLY_IN_B = "only-in-B"


@dataclass(frozen=True)
class DiffEntry:
    """A single classified path that exists in *both* trees but differs.

    ``reason`` is a human-readable explanation of *why* it differs (e.g. the size
    mismatch that preceded the content mismatch). It is informational; the
    authoritative signal is the differing ``sha256``.
    """

    path: str
    reason: str


@dataclass
class Diff:
    """The result of classifying two manifests against each other.

    Attributes
    ----------
    identical:
        Paths present in both trees with byte-identical content (same ``sha256``).
    differing:
        Paths present in both trees whose content differs. Each carries a
        :class:`DiffEntry` with a ``reason``. Sorted by ``path``.
    only_in_a:
        Paths present in tree A but missing from tree B. Sorted.
    only_in_b:
        Paths present in tree B but missing from tree A. Sorted.
    """

    identical: Sequence[str] = field(default_factory=list)
    differing: Sequence[DiffEntry] = field(default_factory=list)
    only_in_a: Sequence[str] = field(default_factory=list)
    only_in_b: Sequence[str] = field(default_factory=list)

    @property
    def missing(self) -> Sequence[str]:
        """All paths that exist in exactly one tree (``only_in_a`` + ``only_in_b``).

        This is the ``missing`` list the report-emission subtask writes to JSON.
        """
        return (*self.only_in_a, *self.only_in_b)

    def counts(self) -> Dict[str, int]:
        """Return ``{status: count}`` for the four buckets, in a fixed order."""
        return {
            IDENTICAL: len(self.identical),
            DIFFERS: len(self.differing),
            ONLY_IN_A: len(self.only_in_a),
            ONLY_IN_B: len(self.only_in_b),
        }


def _diff_reason(a: FileEntry, b: FileEntry) -> str:
    """Explain why two differing artifacts differ (size first, then content)."""
    if a.size != b.size:
        return f"size differs: {a.size} vs {b.size}"
    return "content differs"


def classify(
    manifest_a: Mapping[str, FileEntry],
    manifest_b: Mapping[str, FileEntry],
) -> Diff:
    """Classify every path in two manifests into the four buckets.

    Parameters
    ----------
    manifest_a:
        The reference manifest (``{relpath: FileEntry}`` from
        :func:`~pengyos_repro.manifest.manifest`).
    manifest_b:
        The candidate manifest.

    Returns
    -------
    Diff
        With ``identical``, ``differing``, ``only_in_a`` and ``only_in_b`` lists,
        each sorted by path.
    """
    a = dict(manifest_a)
    b = dict(manifest_b)

    diff = Diff()
    for path in sorted(set(a) | set(b)):
        in_a = path in a
        in_b = path in b
        if in_a and in_b:
            ea, eb = a[path], b[path]
            if ea.sha256 == eb.sha256:
                diff.identical.append(path)
            else:
                diff.differing.append(
                    DiffEntry(path=path, reason=_diff_reason(ea, eb))
                )
        elif in_a:
            diff.only_in_a.append(path)
        else:
            diff.only_in_b.append(path)

    return diff


def classify_trees(
    root_a: str,
    root_b: str,
    *,
    chunk_size: int = 1 << 20,
) -> Diff:
    """Convenience wrapper: walk two trees and classify them.

    Composes :func:`~pengyos_repro.manifest.manifest` with :func:`classify`.
    Prefer :func:`classify` directly when you already have manifests (e.g. after
    applying ignore filters); prefer :func:`classify_trees` when you just have
    two directories on disk.
    """
    from .manifest import manifest

    return classify(manifest(root_a, chunk_size=chunk_size), manifest(root_b, chunk_size=chunk_size))
