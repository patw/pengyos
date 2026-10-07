"""Upstream agent-workspace layout discovery for PengyOS.

The follow-up "pin the pengy-agent's workspace file-write
surface" is blocked until PengyOS defines the agent's real working
layout *upstream* — the profile's writable surface is deliberately
pinned to the workspace root, the flat ``.git`` metadata, the flat
``/tmp/pengy-agent/*`` scratch and the log, and working-tree writes
below the workspace top level are not granted until the concrete path
set exists. As with the model-server port, the path set must never be
invented: a definition has to come from the PengyOS workspace.

This module turns that re-check into a deterministic, tested command —
the layout analogue of :mod:`pengyos_apparmor.portdiscovery` (the skip
rules, the tree walk, the self-match exclusion and the file decode
rule — UTF-8 with ONE leading BOM stripped, ``utf-8-sig`` — are shared
with it):

* :func:`find_agent_workspace_layout` scans a directory tree (the
  PengyOS workspace) for text lines that *define* the agent's working
  layout — deliberately conservative: the line must name a concrete
  path *below* the agent's workspace root
  (:data:`AGENT_WORKSPACE_ROOT`), so a hit is a definition claim a
  human reviews, never an automatic widening of the profile;
* :func:`scan_text` is the pure per-text half, unit-testable without
  any filesystem at all;
* every checkout of this tool is always skipped (its profile, docs and
  test fixtures are full of deliberate workspace paths — self-matches,
  never upstream definitions), and a scan whose *root* is a tool
  checkout reports no candidates;
* binary files, files over ``portdiscovery.MAX_SCAN_BYTES``, and
  build/VCS noise directories (``portdiscovery.SKIP_DIRS``) are
  skipped; and files are decoded as UTF-8 with a *leading BOM
  stripped* (``utf-8-sig``, the port scan's decode rule) — a BOM is a
  file-level encoding marker, not line content, so it must not leak
  into the printed evidence line a human reviews (a ``U+FEFF`` on a
  *later* line is line content, deliberately not stripped; the layout
  pattern is not line-start-anchored, so matching a BOM-marked file
  works either way — the decode rule is about the evidence line, not
  the match).

Nothing here modifies files and nothing ever decides the path set: the
command's job ends at *reporting candidate surfaces*.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Tuple, Union

from .portdiscovery import (
    MAX_SCAN_BYTES,
    SKIP_DIRS,
    TOOL_ROOT,
    is_binary,
    is_tool_checkout,
)

__all__ = [
    "AGENT_WORKSPACE_ROOT",
    "LAYOUT_PATTERN",
    "LayoutDiscoveryError",
    "LayoutCandidate",
    "scan_text",
    "find_agent_workspace_layout",
]

#: The agent's workspace root, exactly as the committed profile
#: ``profiles/pengy-agent`` grants it (``/opt/pengy/workspaces/
#: pengy-agent rw,`` and the flat ``.git`` lines). A layout definition
#: names a path *below* this root; the constant is locked in lockstep
#: to the profile by the test suite so the scan and the profile can
#: never drift apart.
AGENT_WORKSPACE_ROOT = "/opt/pengy/workspaces/pengy-agent"

#: One layout surface: a path token below the workspace root. The first
#: segment character may not be ``/`` (a double slash after the root is
#: not a path), the rest of the token may carry path separators; glob
#: metacharacters (``*``, ``?``, ``[..]``) are allowed — an upstream
#: layout is likely to be written in AppArmor glob form. The left
#: lookbehind rejects tokens embedded in a longer path (a root that is
#: a suffix of some other path, e.g. ``/srv/opt/pengy/...``, is not the
#: agent's workspace).
_SEGMENT = r"[A-Za-z0-9._*?\[\]-][A-Za-z0-9._*?\[\]*/-]*"
LAYOUT_PATTERN = re.compile(
    r"(?<![\w/.-])" + re.escape(AGENT_WORKSPACE_ROOT) + r"/(?P<surface>" + _SEGMENT + r")"
)


class LayoutDiscoveryError(Exception):
    """The scan target is not a directory (or another bad-input state)."""


@dataclass(frozen=True)
class LayoutCandidate:
    """One line that names concrete paths below the agent's workspace.

    ``path`` is the file path relative to the scanned root, ``lineno``
    is 1-based, ``surfaces`` the extracted sub-paths in line order
    (deduplicated, trailing ``.``/``/`` prose punctuation stripped),
    ``line`` the stripped
    source line — the evidence a human reviews before widening the
    profile's write surface.
    """

    path: str
    lineno: int
    surfaces: Tuple[str, ...]
    line: str


def scan_text(text: str) -> list[tuple[int, tuple[str, ...]]]:
    """Pure per-text half of the scan.

    Returns ``(lineno, surfaces)`` pairs (1-based, in line order) for
    every line that names at least one concrete path below
    :data:`AGENT_WORKSPACE_ROOT`. Within a line, surfaces are in line
    order, deduplicated, and carry no trailing ``.`` or ``/``
    (prose punctuation, not path content). A line that names
    the workspace root itself (with nothing below it) is *not* a layout
    definition — the root grant already exists in the profile.
    """
    hits: list[tuple[int, tuple[str, ...]]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        surfaces: list[str] = []
        for m in LAYOUT_PATTERN.finditer(line):
            # A trailing '.' or '/' is prose punctuation (a sentence
            # ending in "…/build/**."), not part of the path.
            surface = m.group("surface").rstrip("./")
            if surface and surface not in surfaces:
                surfaces.append(surface)
        if surfaces:
            hits.append((lineno, tuple(surfaces)))
    return hits


def find_agent_workspace_layout(
    root: Union[str, Path],
    extra_skip: Iterable[Union[str, Path]] = (),
) -> list[LayoutCandidate]:
    """Scan the tree under *root* for agent workspace layout definitions.

    Returns :class:`LayoutCandidate` records sorted by
    ``(path, lineno)``. Skips: non-directories raise
    :class:`LayoutDiscoveryError`; ``portdiscovery.SKIP_DIRS``
    subtrees; every checkout of this tool (any directory containing the
    ``pengyos_apparmor`` package — this repo, vendored copies in sibling
    workspaces, editable-install source trees); anything in
    *extra_skip*; binary files; files larger than
    ``portdiscovery.MAX_SCAN_BYTES``.

    Files are decoded as UTF-8 with a *leading BOM stripped*
    (``utf-8-sig``): a BOM is a file-level encoding marker, not line
    content — stripped, the printed evidence line never carries an
    invisible ``U+FEFF``; a ``U+FEFF`` on a later line is line
    content and stays in place (matching is unaffected: the layout
    pattern is not line-start-anchored, so this decode rule is about
    the evidence a human reviews, not about finding the candidate —
    the same rule the port scan applies).

    A *root* that is itself a tool checkout yields no candidates at
    all: every line under it is part of this tool, so every hit would
    be a self-match, and "no upstream layout defined" is the honest
    answer.

    The scan is read-only and never decides which surfaces the profile
    should grant — multiple distinct candidates are reported for a
    human to resolve against the agent's real working layout.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise LayoutDiscoveryError(f"not a directory: {root}")
    if is_tool_checkout(root_path):
        return []

    skip_paths = {TOOL_ROOT}
    for extra in extra_skip:
        skip_paths.add(Path(extra).resolve() if Path(extra).exists() else Path(extra))

    candidates: list[LayoutCandidate] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Prune noise and skipped subtrees in place so os.walk never descends.
        kept = []
        for name in dirnames:
            child = Path(dirpath) / name
            if name in SKIP_DIRS:
                continue
            if is_tool_checkout(child):
                # A checkout of this tool (any of them): its profile,
                # docs and fixtures are self-matches, never upstream
                # layout definitions.
                continue
            try:
                child_resolved = child.resolve()
            except OSError:  # broken symlink etc. — do not descend
                continue
            if any(
                child_resolved == skip or skip in child_resolved.parents
                for skip in skip_paths
            ):
                continue
            kept.append(name)
        dirnames[:] = kept

        for name in filenames:
            fpath = Path(dirpath) / name
            try:
                if fpath.stat().st_size > MAX_SCAN_BYTES:
                    continue
                data = fpath.read_bytes()
            except OSError:
                continue
            if is_binary(data):
                continue
            # utf-8-sig: strip ONE leading BOM if present — the shared
            # decode rule with the port scan. A BOM is a file-level
            # encoding marker, not line content: stripped, the printed
            # evidence line never carries an invisible U+FEFF. A
            # U+FEFF on a later line is line content and stays in
            # place; for a BOM-less file utf-8-sig decodes identically
            # to utf-8. (The pattern is not line-start-anchored, so
            # matching works either way — this is the evidence line.)
            text = data.decode("utf-8-sig", errors="replace")
            for lineno, surfaces in scan_text(text):
                rel = fpath.relative_to(root_path).as_posix()
                line = text.splitlines()[lineno - 1].strip()
                candidates.append(LayoutCandidate(rel, lineno, surfaces, line))

    candidates.sort(key=lambda c: (c.path, c.lineno))
    return candidates
