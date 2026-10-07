"""Explain *which device pulled in a* ``CONFIG_`` symbol for a profile.

Given a validated hwprofile
document, a fragments directory and a single ``CONFIG_`` symbol, answer the
read-only question

    *which device in this machine's profile pulled in the fragment that carries
    this symbol, and what is the symbol's final (merged) value?*

How a symbol reaches the image
-------------------------------
The tool's data flow is

    profile  ->  mapper  ->  selected fragments  ->  later-wins merge  ->  CONFIG set

``select`` reports the merged CONFIG set; ``exclude`` reports what the machine
*omitted*. ``explain`` traces a carried symbol **backwards** along that flow:

1. the profile's devices are mapped to fragments (see
   :func:`pengyos_hwprofile_kernel.mapper.select_fragments`); each device that
   matched a rule *selected* a fragment;
2. among the **selected** fragments, the ones that assign the queried symbol are
   the fragments that "carry" it; and
3. for each such fragment, the device(s) that selected it are the devices that
   *pulled in* the symbol.

Only **selected** fragments are considered — a fragment the machine did not
select did not pull any device's symbol into *this* image. A symbol no selected
fragment sets is not an error: it is simply not pulled in by this machine, and
the report says so.

Final value
-----------
The symbol's **final value** uses the same later-wins precedence as
:func:`pengyos_hwprofile_kernel.select.compose`: the selected fragments are
walked in sorted-by-name order and the last one to assign the symbol wins. The
report carries that value and the fragment that supplied it.

Standard library only at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pengyos_hwprofile_kernel import mapper
from pengyos_hwprofile_kernel import select as select_mod


def _device_label(device: Mapping[str, Any], section: str) -> str:
    """A short, stable human label for a device (for report rendering).

    Prefers the most identifying field per section (network ``interface``,
    GPU/storage ``model``, CPU ``model``), falling back to ``vendor`` then
    ``bus``, then the section name.
    """

    for key in ("interface", "model", "vendor", "bus"):
        value = device.get(key)
        if isinstance(value, str) and value:
            return value
    return section


@dataclass(frozen=True)
class DevicePull:
    """One device that pulled a fragment into the machine's image.

    :ivar device: the original device dict from the profile (for reporting).
    :ivar section: the profile section the device came from.
    :ivar fragment: the selected fragment basename (e.g. ``qemu``).
    :ivar label: a short human label for the device (see
        :func:`_device_label`).
    """

    device: Mapping[str, Any]
    section: str
    fragment: str
    label: str


@dataclass
class SymbolFragment:
    """A selected fragment that sets the queried symbol, and the devices that
    pulled it in.

    :ivar fragment: the selected fragment basename (e.g. ``qemu``).
    :ivar value: the value this fragment assigns the symbol to.
    :ivar line_no: the 1-based line number within the fragment.
    :ivar devices: the device(s) that selected this fragment, in profile order.
    """

    fragment: str
    value: str
    line_no: int
    devices: tuple[DevicePull, ...]


@dataclass
class ExplainReport:
    """The outcome of explaining a symbol for a profile.

    :ivar symbol: the queried ``CONFIG_`` symbol.
    :ivar fragments: the selected fragments that set the symbol, sorted by
        fragment name; each carries its value, line number and the device(s)
        that pulled it in.
    :ivar final_value: the symbol's final merged value (later-wins), or ``None``
        when no selected fragment sets it.
    :ivar final_source: the fragment that supplied the final value, or ``None``.
    :ivar profile: the compose profile label.
    :ivar unknown: unmapped devices (from the mapper) — reported, never dropped.
    :ivar missing: selected fragment basenames absent from the fragments dir
        (from the compose step). Empty when every selected fragment resolved.

    :attr:`ok` is always ``True``: a symbol that no selected fragment sets is a
    clean answer (``found`` is ``False``), not an error. :attr:`found` is
    ``True`` when a selected fragment set the symbol.
    """

    symbol: str
    fragments: tuple[SymbolFragment, ...] = ()
    final_value: str | None = None
    final_source: str | None = None
    profile: str = "hwprofile-kernel"
    unknown: tuple[mapper.DeviceMatch, ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Always ``True`` — a symbol with no pulling fragment is a clean
        answer, not an error."""
        return True

    @property
    def exit_code(self) -> int:
        """Process exit code: always 0 — the query is always answerable."""
        return 0

    @property
    def found(self) -> bool:
        """``True`` when a selected fragment set the symbol (was pulled in)."""
        return bool(self.fragments)


def _selected_library(
    fragments_dir: str | Path,
    selected: tuple[str, ...],
) -> dict[str, Any]:
    """Build a ``{basename: Fragment}`` library for the *selected* fragments.

    Mirrors :func:`pengyos_hwprofile_kernel.select.load_library` but keeps only
    the fragments the machine actually selected, so a symbol's providers are the
    fragments that pulled it into *this* image.
    """

    from pengyos_kernel_fragments.fragments import load_directory as _load

    library = {f.name: f for f in _load(fragments_dir)}
    wanted = {f"{name}.fragment" for name in selected}
    return {name: frag for name, frag in library.items() if name in wanted}


def explain(
    document: Mapping[str, Any],
    fragments_dir: str | Path,
    symbol: str,
    *,
    profile: str = "hwprofile-kernel",
) -> ExplainReport:
    """Explain which device pulled *symbol* into *document*'s image.

    Parameters:
        document: A validated hwprofile document (the plain dict returned by
            :func:`pengyos_hwprofile_kernel.profiles.load_profile`).
        fragments_dir: Directory of ``*.fragment`` files to compose from.
        symbol: The ``CONFIG_`` symbol to explain.
        profile: The compose profile label (surfaced on the report).

    Returns:
        An :class:`ExplainReport`. :attr:`fragments` lists the selected
        fragments that set the symbol (each with the device(s) that pulled it
        in); :attr:`final_value` / :attr:`final_source` give the later-wins
        value; :attr:`found` is ``False`` when no selected fragment sets it.
    """

    fragments_dir = Path(fragments_dir)

    # Map the profile's devices to the fragments they selected, and compose
    # them (later-wins). The compose report gives the selected fragment set and
    # the merged CONFIG set (whose entries carry their winning fragment source).
    selection = mapper.select_fragments(document)
    compose_report = select_mod.compose(
        document, fragments_dir, profile=profile
    )

    library = _selected_library(fragments_dir, selection.fragments)

    # The fragment basename -> the device(s) that selected it (profile order).
    pull_by_fragment: dict[str, list[DevicePull]] = {}
    for match in selection.matches:
        if match.fragment is None:
            continue
        pull_by_fragment.setdefault(match.fragment, []).append(
            DevicePull(
                device=match.device,
                section=match.section,
                fragment=match.fragment,
                label=_device_label(match.device, match.section),
            )
        )

    # The selected fragments that assign the symbol, in sorted-by-name order
    # (the same order the later-wins merge walks).
    seting = sorted(
        name for name, frag in library.items() if symbol in frag.values
    )

    symbol_fragments: list[SymbolFragment] = []
    final_value: str | None = None
    final_source: str | None = None
    for key in seting:
        entry = library[key].values[symbol]
        name = key[: -len(".fragment")] if key.endswith(".fragment") else key
        symbol_fragments.append(
            SymbolFragment(
                fragment=name,
                value=entry.value,
                line_no=entry.line_no,
                devices=tuple(pull_by_fragment.get(name, ())),
            )
        )
        # Later-wins: the last fragment in sorted order supplies the final value.
        final_value = entry.value
        final_source = name

    return ExplainReport(
        symbol=symbol,
        fragments=tuple(symbol_fragments),
        final_value=final_value,
        final_source=final_source,
        profile=profile,
        unknown=selection.unknown,
        missing=compose_report.missing,
    )


def render_explain(report: ExplainReport) -> str:
    """Render an :class:`ExplainReport` as the ``explain`` command prints.

    When the symbol was pulled in (a selected fragment set it)::

        <SYMBOL>=<final-value> (from <final_source>):
        set by <N> selected fragment(s):
          <fragment> (line <line>): =<value>
            pulled in by <device-label> [<section>]  (xM)
        When no selected fragment set it::

        <SYMBOL>: not pulled in by this machine (no selected fragment sets it)
    """

    if not report.found:
        return (
            f"{report.symbol}: not pulled in by this machine "
            f"(no selected fragment sets it)\n"
        )

    header = (
        f"{report.symbol}={report.final_value} "
        f"(from {report.final_source}):"
    )
    lines = [header]
    lines.append(f"set by {len(report.fragments)} selected fragment(s):")
    for sym in report.fragments:
        devices = sym.devices
        if devices:
            labels = ", ".join(
                f"{d.label} [{d.section}]" for d in devices
            )
            suffix = f"  (x{len(devices)})" if len(devices) > 1 else ""
            lines.append(
                f"  {sym.fragment} (line {sym.line_no}): ={sym.value}"
            )
            lines.append(f"    pulled in by {labels}{suffix}")
        else:
            # A selected fragment that sets the symbol but matched no device
            # (e.g. a base fragment) — note it so the report stays honest.
            lines.append(
                f"  {sym.fragment} (line {sym.line_no}): ={sym.value} "
                f"(no device selected this fragment)"
            )
    return "\n".join(lines) + "\n"
