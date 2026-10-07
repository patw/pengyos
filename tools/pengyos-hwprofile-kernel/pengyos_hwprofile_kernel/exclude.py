"""Emit an *excluded drivers* report for a profile.

Given a validated hwprofile document,
report the kernel **symbols intentionally omitted** for *this* machine.

The report answers the read-only question the ``select`` command does not:
*"the generic PengyOS kernel carries every driver; which of those did *this*
machine's profile leave out?"*

Baseline (the "full" kernel)
----------------------------
The **baseline** is the union of every ``CONFIG_`` symbol set by *every*
fragment in the fragments directory — the set of drivers/services the tool can
enable, i.e. the generic/default image the kernel currently is. The
``select`` command's merged set is the subset *this* machine needs. The
excluded report is the set difference:

    excluded = baseline − selected

A symbol is excluded when it is set somewhere in the baseline but is **not** set
at all in the machine's merged config. A symbol the machine explicitly disables
(``=n``) is likewise excluded — the machine chose not to carry it.

The baseline is derived from the fragments directory, so it grows with the
library: adding a fragment widens the "full kernel" and the report reflects the
newly-available driver as excluded until a profile selects it. Deterministic:
symbols are sorted, and each symbol's providing fragment(s) are sorted too.

Standard library only at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pengyos_hwprofile_kernel import mapper
from pengyos_hwprofile_kernel import select as select_mod


@dataclass(frozen=True)
class ExcludedEntry:
    """One symbol the machine intentionally does not carry.

    :ivar symbol: the excluded ``CONFIG_`` symbol.
    :ivar value: the value it holds in the baseline (the "full" kernel).
    :ivar sources: the fragment(s) that set the symbol in the baseline, sorted.
    """

    symbol: str
    value: str
    sources: tuple[str, ...]


@dataclass
class ExcludedReport:
    """The excluded-drivers report for a profile.

    :ivar included: the machine's merged symbols (the set it carries), sorted.
    :ivar excluded: the omitted symbols (baseline − included), sorted; each
        carries its baseline value and the fragment(s) that would provide it.
    :ivar baseline: the full symbol set (baseline), sorted.
    :ivar profile: the compose profile label.
    :ivar missing: selected fragment basenames absent from the fragments dir
        (from the compose step). Empty when every selected fragment resolved.
    :ivar unknown: devices that matched no mapping rule (from the compose
        step) — reported, never dropped. Empty when every device mapped.

    :attr:`ok` is ``True`` when every selected fragment resolved — the included
    set is then the real one. When a fragment is missing the report is still
    produced (the machine carries no config, so everything is excluded) but
    ``ok`` is ``False`` so the caller can refuse it.
    """

    included: tuple[str, ...] = ()
    excluded: tuple[ExcludedEntry, ...] = ()
    baseline: tuple[str, ...] = ()
    profile: str = "hwprofile-kernel"
    missing: tuple[str, ...] = ()
    unknown: tuple[mapper.DeviceMatch, ...] = ()

    @property
    def ok(self) -> bool:
        """``True`` when every selected fragment resolved (included set is real)."""
        return not self.missing

    @property
    def exit_code(self) -> int:
        """Process exit code: 0 when the report was produced, 2 on a missing
        fragment (the same missing-fragment error as ``select``)."""
        return 0 if self.ok else 2


def baseline_symbols(
    library: Mapping[str, Any],
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    """Return ``(symbol, value, (sources))`` for the baseline "full" kernel.

    The baseline is the union of every ``CONFIG_`` symbol set by *every* fragment
    in *library* (the ``{name: Fragment}`` view :func:`select_mod.load_library`
    builds). A symbol set by several fragments lists all of them (sorted). The
    value reported is the last distinct value alphabetically among the sources
    (the deterministic tie-break; every bundled fragment sets its symbols to
    ``y``, so this is stable).

    Returns the rows sorted by symbol.
    """

    # symbol -> {value: set-of-sources} as seen across the whole library.
    by_symbol: dict[str, dict[str, set[str]]] = {}
    for name in sorted(library):
        fragment = library[name]
        for symbol, entry in fragment.values.items():
            by_symbol.setdefault(symbol, {}).setdefault(entry.value, set()).add(
                name
            )

    ordered: dict[str, tuple[str, tuple[str, ...]]] = {}
    for symbol in sorted(by_symbol):
        values = sorted(by_symbol[symbol])
        value = values[-1]  # deterministic tie-break; bundled values are "y".
        sources: set[str] = set()
        for sources_by_value in by_symbol[symbol].values():
            sources |= sources_by_value
        ordered[symbol] = (value, tuple(sorted(sources)))

    return tuple(
        (symbol, info[0], info[1]) for symbol, info in ordered.items()
    )


def compute_excluded(
    document: Mapping[str, Any],
    fragments_dir: str | Path,
    *,
    profile: str = "hwprofile-kernel",
) -> ExcludedReport:
    """Compute the excluded-drivers report for *document*.

    Parameters:
        document: A validated hwprofile document (the plain dict returned by
            :func:`pengyos_hwprofile_kernel.profiles.load_profile`).
        fragments_dir: Directory of ``*.fragment`` files to compose from.
        profile: The compose profile label (surfaced on the report).

    Returns:
        An :class:`ExcludedReport`. :attr:`included` is the machine's merged
        symbols; :attr:`excluded` is baseline − included (each entry carries its
        baseline value and the fragment(s) that provide it); :attr:`baseline` is
        the full symbol set. The included set is derived from
        :func:`pengyos_hwprofile_kernel.select.compose`, so a missing fragment is
        *not* a hard error here — the report is still produced (the machine
        simply carries no config), but :attr:`ExcludedReport.ok` stays ``True``.

    The report is only meaningful when the profile composed; a caller that wants
    to refuse a broken profile should check the compose report's ``missing``
    separately (see the ``exclude`` CLI).
    """

    compose_report = select_mod.compose(document, fragments_dir, profile=profile)

    library = select_mod.load_library(fragments_dir)
    base = baseline_symbols(library)
    baseline_symbols_set = tuple(symbol for symbol, _, _ in base)

    # A symbol is "enabled" (carried) only when the machine's merged value is a
    # real setting: "y" (built-in) or "m" (module). A symbol the machine leaves
    # absent, or explicitly disables with "=n", is intentionally omitted.
    included = {
        entry.symbol
        for entry in compose_report.merged
        if entry.value in ("y", "m")
    }
    excluded = tuple(
        ExcludedEntry(symbol, value, sources)
        for symbol, value, sources in base
        if symbol not in included
    )

    return ExcludedReport(
        included=tuple(sorted(included)),
        excluded=excluded,
        baseline=baseline_symbols_set,
        profile=profile,
        missing=compose_report.missing,
        unknown=compose_report.unknown,
    )


def render_excluded(report: ExcludedReport) -> str:
    """Render the excluded-drivers report as text.

    A one-line header names the profile and the included/excluded counts, then
    one line per excluded symbol::

        <SYMBOL>=<baseline-value>   (source1.fragment[, source2.fragment])

    An empty excluded set prints just the header (no trailing blank line beyond
    the header's own newline). The output is newline-terminated.
    """

    lines = [
        f"excluded drivers — {report.profile}: "
        f"{len(report.included)} included, {len(report.excluded)} excluded"
    ]
    lines.extend(
        f"{e.symbol}={e.value}  ({', '.join(e.sources)})"
        for e in report.excluded
    )
    return "\n".join(lines) + "\n"
