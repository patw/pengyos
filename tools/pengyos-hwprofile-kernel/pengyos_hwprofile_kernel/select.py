"""Compose a profile's selected fragments into a deterministic defconfig.

Given a validated hwprofile document, map
its devices to kernel fragments (via :func:`pengyos_hwprofile_kernel.mapper.
select_fragments`) and merge those fragments into a single ``CONFIG`` set using
the ``pengyos-kernel-fragments`` **later-wins** grammar.

The merge reuses the sibling library's :func:`merge_fragments` (later-wins with
conflict warnings) and emits the merged set in the same ``CONFIG_X=value`` line
format the fragments use — sorted by symbol, so the output is byte-stable
across runs.

The fragments merged here come from a **fragments directory** (the
``--fragments DIR`` argument): a directory of ``*.fragment`` files, the same
shape the sibling composer expects ("fragments live in a directory"). This tool
ships a bundled ``fragments/`` directory with every fragment its mapping
references, so ``select`` is self-contained; a caller may point ``--fragments``
at any other directory of ``*.fragment`` files.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pengyos_hwprofile_kernel import mapper


@dataclass
class SelectReport:
    """The outcome of composing a profile's selected fragments.

    :ivar fragments: the selected fragment basenames in merge order (the
        mapper's sorted, deduplicated set).
    :ivar merged: the merged ``CONFIG`` entries, sorted by symbol.
    :ivar warnings: later-wins override warnings, in the order they fired.
    :ivar missing: selected fragment basenames not found in the library — a
        real error (the mapping references a fragment that does not exist in
        ``--fragments``).
    :ivar unknown: devices that matched no rule (from the mapper) — reported so
        the caller can warn; never dropped.
    :ivar profile: the compose profile label.

    :attr:`ok` is ``True`` when every selected fragment resolved (no missing).
    """

    fragments: tuple[str, ...] = ()
    merged: tuple = ()
    warnings: tuple = ()
    missing: tuple[str, ...] = ()
    unknown: tuple[mapper.DeviceMatch, ...] = ()
    profile: str = "hwprofile-kernel"

    @property
    def ok(self) -> bool:
        """``True`` when every selected fragment resolved in the library."""
        return not self.missing

    @property
    def exit_code(self) -> int:
        """Process exit code: 0 when the defconfig composed, 2 on a missing
        fragment (the mapping references a fragment absent from ``--fragments``)."""
        return 0 if self.ok else 2


def load_library(fragments_dir: str | Path) -> dict:
    """Build the ``{basename: Fragment}`` library from *fragments_dir*.

    Mirrors the sibling composer's library view: every ``*.fragment`` file in
    the directory (recursively) becomes a library entry keyed by its basename.
    The import is lazy so this module stays importable even when the sibling is
    absent from ``sys.path``; the sibling is required at call time (it is on
    ``sys.path`` in tests via ``conftest.py`` and in production).
    """

    from pengyos_kernel_fragments.fragments import load_directory as _load_directory

    return {f.name: f for f in _load_directory(fragments_dir)}


def compose(
    document: Mapping[str, Any],
    fragments_dir: str | Path,
    *,
    base_fragments: Mapping[str, str] | None = None,
    profile: str = "hwprofile-kernel",
) -> SelectReport:
    """Map *document*'s devices to fragments and compose them.

    Parameters:
        document: A validated hwprofile document (the plain dict returned by
            :func:`pengyos_hwprofile_kernel.profiles.load_profile`).
        fragments_dir: Directory of ``*.fragment`` files to compose from.
        base_fragments: Optional ``{section: fragment}`` map of base fragments
            always included (see
            :func:`pengyos_hwprofile_kernel.mapper.select_fragments`).
        profile: The compose profile label (surfaced on each merged entry and
            in :attr:`SelectReport.profile`).

    Returns:
        A :class:`SelectReport`. :attr:`SelectReport.fragments` is the sorted,
        deduplicated set of selected fragment basenames; :attr:`merged` is the
        later-wins merged CONFIG set; :attr:`warnings` are the later-wins
        overrides; :attr:`missing` names any selected fragment absent from the
        library; :attr:`unknown` lists unmapped devices.
    """

    from pengyos_kernel_fragments.compose import merge_fragments as _merge

    fragments_dir = Path(fragments_dir)
    library = load_library(fragments_dir)

    selection = mapper.select_fragments(document, base_fragments=base_fragments)
    compose_report = _merge(selection.fragments, library, profile)

    return SelectReport(
        fragments=selection.fragments,
        merged=compose_report.merged,
        warnings=compose_report.warnings,
        missing=compose_report.missing,
        unknown=selection.unknown,
        profile=profile,
    )


def render_defconfig(report: SelectReport) -> str:
    """Render the merged CONFIG set as the defconfig fragment text.

    One ``CONFIG_X=value`` line per merged entry, sorted by symbol, with a
    trailing newline (matching the sibling composer's ``compose --out`` output).
    An empty set renders as an empty string (no trailing newline).
    """

    merged = "\n".join(str(entry) for entry in report.merged)
    return merged + "\n" if merged else ""


def render_warnings(report: SelectReport) -> str:
    """Render the later-wins conflict warnings as text (one line each)."""

    return "\n".join(w.format() for w in report.warnings)


def render_missing(report: SelectReport) -> str | None:
    """Return the missing-fragment usage message, or ``None`` when none."""

    if report.ok:
        return None
    return (
        f"error: profile references missing fragment(s): "
        f"{', '.join(report.missing)} "
        f"(not found in the fragments directory)"
    )
