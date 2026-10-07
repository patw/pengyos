"""Canonical witness layout as data (implementation task 2).

This module is the single source of truth for *what* the witness layout is: for
every artifact kind it records the conventional name this tool writes under
``<build>/.witness/`` and the alias patterns that ``pengyos-build`` actually
emits. It deliberately does **not** scan a filesystem — that is task 3
(discovery). Here we only define the data and the tiny matching helper discovery
needs.

## The layout

``pengyos-build`` writes its SBOM and CycloneDX documents under
``images/<target>.<fmt>.json`` (``<target>`` varies, e.g. ``pengyos-x86_64``),
the signature rides alongside as ``images/<target>.spdx.json.sig``, and the repro
report is written at the build root. The external witness, however, looks for
conventional names under ``<build>/.witness/``:

| kind             | canonical            |
| ---------------- | -------------------- |
| ``sbom``         | ``sbom.spdx.json``   |
| ``cyclonedx``    | ``sbom.cyclonedx.json`` |
| ``sbom_signature`` | ``sbom.spdx.json.sig`` |
| ``repro_report`` | ``repro-report.json`` |

Each :class:`ArtifactSpec` encodes, alongside its canonical name, the ordered
alias globs that map pengyos-build's real output back to that kind. Discovery
(task 3) walks the build dir and, for each kind, takes the first alias that
matches an existing file.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass(frozen=True)
class ArtifactSpec:
    """One artifact in the canonical witness layout.

    Attributes:
        kind:      Logical artifact kind, e.g. ``sbom``.
        canonical: Conventional name under ``<build>/.witness/``.
        aliases:   Ordered glob patterns (relative to the build dir) that
                   ``pengyos-build`` actually emits. The first that matches an
                   existing file wins during discovery. ``*`` matches any run of
                   characters *including* ``/``, so ``images/*.spdx.json`` also
                   matches ``images/pengyos-x86_64.spdx.json``.
    """

    kind: str
    canonical: str
    aliases: tuple[str, ...]

    def matches(self, rel_path: str) -> bool:
        """Return ``True`` if build-relative ``rel_path`` matches an alias."""
        normalized = rel_path.replace("\\", "/")
        return any(fnmatch.fnmatch(normalized, pat) for pat in self.aliases)


# The canonical witness layout, as data. Ordered: sbom, cyclonedx, signature,
# repro report. Order matters only for discovery tie-breaking when a single file
# could match two kinds (it cannot here, because the formats are disjoint).
CANONICAL_LAYOUT: tuple[ArtifactSpec, ...] = (
    ArtifactSpec(
        kind="sbom",
        canonical="sbom.spdx.json",
        aliases=(
            "images/*.spdx.json",
            "sbom.spdx.json",
            "*.spdx.json",
        ),
    ),
    ArtifactSpec(
        kind="cyclonedx",
        canonical="sbom.cyclonedx.json",
        aliases=(
            "images/*.cyclonedx.json",
            "images/*.cdx.json",
            "sbom.cyclonedx.json",
            "*.cyclonedx.json",
            "*.cdx.json",
        ),
    ),
    ArtifactSpec(
        kind="sbom_signature",
        canonical="sbom.spdx.json.sig",
        aliases=(
            "images/*.spdx.json.sig",
            "sbom.spdx.json.sig",
            "*.spdx.json.sig",
        ),
    ),
    ArtifactSpec(
        kind="repro_report",
        canonical="repro-report.json",
        aliases=(
            "repro-report.json",
            "images/repro-report.json",
            "images/*.repro.json",
            "repro-report.txt",
            "images/*.repro.txt",
            "repro.txt",
        ),
    ),
)


def spec_for(kind: str) -> ArtifactSpec:
    """Return the :class:`ArtifactSpec` for ``kind`` (raises ``ValueError``)."""
    for spec in CANONICAL_LAYOUT:
        if spec.kind == kind:
            return spec
    available = ", ".join(spec.kind for spec in CANONICAL_LAYOUT)
    raise ValueError(f"unknown artifact kind {kind!r}; available: {available}")


def canonical_names() -> dict[str, str]:
    """Return ``{kind: conventional_name}`` for the human/table view."""
    return {spec.kind: spec.canonical for spec in CANONICAL_LAYOUT}


def iter_files(build_dir: str | Path) -> Iterator[str]:
    """Yield build-relative POSIX paths of every regular file under ``build_dir``.

    The paths are sorted for deterministic discovery ordering. ``build_dir`` itself
    is never yielded even if it points at a file.
    """
    root = Path(build_dir)
    if root.is_file():
        return
    rel_paths = [
        rel.relative_to(root).as_posix()
        for rel in sorted(root.rglob("*"))
        if rel.is_file() and rel != root
    ]
    yield from rel_paths


def _lowest_matching_alias_index(spec: ArtifactSpec, rel_path: str) -> int | None:
    """Return the index of the lowest-index alias matching ``rel_path``, else ``None``.

    Aliases are ordered most-specific first (``images/*.spdx.json`` before the broad
    ``*.spdx.json``), so the returned index measures *how specific* the match was:
    a lower index is a more specific, more trustworthy match.
    """
    normalized = rel_path.replace("\\", "/")
    for index, pat in enumerate(spec.aliases):
        if fnmatch.fnmatch(normalized, pat):
            return index
    return None


def find_matching(build_dir: str | Path, kind: str) -> str | None:
    """Return the build-relative path matching ``kind``'s aliases, preferring the
    most specific (lowest-index) alias, else ``None``.

    This is the primitive task 3 (discovery) builds on. Among every existing file
    that matches any of ``kind``'s alias globs, the winner is the one matching the
    *lowest-index* (most specific) alias — so ``images/*.spdx.json`` beats the broad
    ``*.spdx.json`` that would otherwise grab a spurious sibling SPDX doc (e.g. a
    host-package ``Misc/externals.spdx.json`` that sorts before ``images/``). When
    several files match the same lowest-index alias, the earliest in sorted path
    order wins, keeping discovery deterministic.
    """
    spec = spec_for(kind)
    best: tuple[int, str] | None = None  # (lowest_alias_index, path)
    for rel in iter_files(build_dir):
        alias_index = _lowest_matching_alias_index(spec, rel)
        if alias_index is None:
            continue
        candidate = (alias_index, rel)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None else None


def kinds() -> Iterable[str]:
    """Return the ordered artifact kinds in the canonical layout."""
    return (spec.kind for spec in CANONICAL_LAYOUT)
