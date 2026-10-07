"""Fragment delta between two profiles — hardware-change detection.

Given two validated
hwprofile documents, report the **fragment delta** between them — the fragments
the second machine carries that the first does not (**added**) and the fragments
the first carried that the second does not (**removed**).

Because the tool maps hardware to fragments, the fragment delta *is* the
hardware change: a fragment added (or removed) signals that a device class
changed between the two profiles (a new NIC, a different GPU vendor, an added
NVMe controller, …). The diff therefore answers

    *what hardware does profile B have that profile A did not, and vice versa?*

Semantics
---------
Each profile is mapped to its selected fragment set (via
:func:`pengyos_hwprofile_kernel.mapper.select_fragments`). Going ``A -> B``:

* **added** = `fragments(B) - fragments(A)` — fragments B carries that A did not.
* **removed** = `fragments(A) - fragments(B)` — fragments A carried that B does
  not.

A fragment that appears in both profiles is unchanged and is not reported. Each
changed fragment carries the symbols it sets (sorted), so the delta is
actionable without a second `select`.

Standard library only at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pengyos_hwprofile_kernel import mapper
from pengyos_hwprofile_kernel import select as select_mod


@dataclass(frozen=True)
class FragmentDelta:
    """One fragment added to, or removed between, two profiles.

    :ivar fragment: the fragment basename.
    :ivar kind: ``"added"`` (in B, not A) or ``"removed"`` (in A, not B).
    :ivar symbols: the symbols this fragment sets (sorted), for context.
    """

    fragment: str
    kind: str
    symbols: tuple[str, ...]


@dataclass
class DiffReport:
    """The fragment delta between two profiles.

    :ivar a: the profile A label.
    :ivar b: the profile B label.
    :ivar added: fragments B carries that A does not (going ``A -> B``), sorted
        by fragment name; each carries the symbols it sets.
    :ivar removed: fragments A carried that B does not, sorted by fragment name.
    :ivar a_fragments: the full selected fragment set of A (sorted).
    :ivar b_fragments: the full selected fragment set of B (sorted).
    :ivar unknown: unmapped devices across both profiles (reported, never
        dropped).
    :ivar missing: selected fragments absent from the fragments directory (from
        the compose step).

    :attr:`changed` is the total number of changed fragments
    (``len(added) + len(removed)``); ``0`` means the two profiles select the
    same fragment set.
    """

    a: str
    b: str
    added: tuple[FragmentDelta, ...] = ()
    removed: tuple[FragmentDelta, ...] = ()
    a_fragments: tuple[str, ...] = ()
    b_fragments: tuple[str, ...] = ()
    unknown: tuple[mapper.DeviceMatch, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def changed(self) -> int:
        """Total number of changed fragments (added + removed)."""
        return len(self.added) + len(self.removed)

    @property
    def identical(self) -> bool:
        """``True`` when the two profiles select the same fragment set."""
        return self.changed == 0


def _symbols_for(
    library: Mapping[str, Any],
    fragment: str,
) -> tuple[str, ...]:
    """Return the sorted symbol names a selected *fragment* sets."""

    key = f"{fragment}.fragment"
    fragment_obj = library.get(key)
    if fragment_obj is None:
        return ()
    return tuple(sorted(fragment_obj.values))


def diff(
    document_a: Mapping[str, Any],
    document_b: Mapping[str, Any],
    fragments_dir: str | Path,
    *,
    profile_a: str = "profileA",
    profile_b: str = "profileB",
) -> DiffReport:
    """Report the fragment delta between *document_a* and *document_b*.

    Parameters:
        document_a: A validated hwprofile document (profile A).
        document_b: A validated hwprofile document (profile B).
        fragments_dir: Directory of ``*.fragment`` files to compose from.
        profile_a / profile_b: Labels for A / B (surfaced on the report).

    Returns:
        A :class:`DiffReport`. :attr:`added` is `fragments(B) - fragments(A)`;
        :attr:`removed` is `fragments(A) - fragments(B)`; each changed fragment
        carries the symbols it sets.
    """

    fragments_dir = Path(fragments_dir)

    selection_a = mapper.select_fragments(document_a)
    selection_b = mapper.select_fragments(document_b)

    library = select_mod.load_library(fragments_dir)

    set_a = set(selection_a.fragments)
    set_b = set(selection_b.fragments)

    added = tuple(
        FragmentDelta(
            fragment=name,
            kind="added",
            symbols=_symbols_for(library, name),
        )
        for name in sorted(set_b - set_a)
    )
    removed = tuple(
        FragmentDelta(
            fragment=name,
            kind="removed",
            symbols=_symbols_for(library, name),
        )
        for name in sorted(set_a - set_b)
    )

    unknown = tuple(selection_a.unknown) + tuple(selection_b.unknown)
    # Library keys are "<basename>.fragment"; a selected fragment is missing
    # when that suffixed key is absent from the library.
    missing = tuple(
        sorted(
            name
            for name in (set_a | set_b)
            if f"{name}.fragment" not in library
        )
    )

    return DiffReport(
        a=profile_a,
        b=profile_b,
        added=added,
        removed=removed,
        a_fragments=tuple(sorted(set_a)),
        b_fragments=tuple(sorted(set_b)),
        unknown=unknown,
        missing=missing,
    )


def render_diff(report: DiffReport) -> str:
    """Render a :class:`DiffReport` as the ``diff`` command prints.

    A header names the two profiles and the change counts, then one line per
    changed fragment::

        + <fragment>   (added: N new symbol(s))
          <symbol>
        - <fragment>   (removed: M old symbol(s))
          <symbol>
    When the profiles are identical, just the (zero-change) header.
    """

    lines = [
        f"hardware change {report.a} -> {report.b}: "
        f"{len(report.added)} added, {len(report.removed)} removed",
    ]
    for delta in report.added:
        lines.append(f"+ {delta.fragment}   (added: {len(delta.symbols)} new)")
        lines.extend(f"  {s}" for s in delta.symbols)
    for delta in report.removed:
        lines.append(
            f"- {delta.fragment}   (removed: {len(delta.symbols)} old)"
        )
        lines.extend(f"  {s}" for s in delta.symbols)
    return "\n".join(lines) + "\n"
