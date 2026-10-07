"""Explain which fragment(s) set a ``CONFIG_`` symbol and its final value.

Implements the ``explain`` command and complements
:mod:`pengyos_kernel_fragments.validate` and :mod:`pengyos_kernel_fragments.compose`.

``validate`` *asserts* that a library is well-formed and ``compose`` *merges* a
named profile's fragments into one deterministic ``CONFIG`` set; ``explain``
answers a read-only question — *which fragment(s) set a given symbol, and what
is its final (merged) value?*

Semantics
---------
``explain`` scans the ``*.fragment`` files in a directory and reports, for a
single ``CONFIG_`` symbol:

* every fragment that assigns the symbol, as ``(source, value, line_no)``; and
* the symbol's **final value**, using the same later-wins precedence as
  :func:`pengyos_kernel_fragments.compose.merge_fragments`.

The merge order is:

* the directory's fragments sorted by name when no profile is given (the same
  deterministic order :func:`load_directory` returns them in); or
* the named profile's fragment order when ``--profile`` is supplied — the final
  value is then that profile's later-wins value.

A missing/unknown profile (or a profile that references an unresolved fragment)
is a usage error, mirroring ``compose``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .compose import ComposeReport, _resolve_name, compose_directory, render_error
from .fragments import Fragment, load_directory


@dataclass(frozen=True)
class Assignment:
    """A single fragment assignment of a symbol.

    :ivar source: the fragment basename that set the symbol.
    :ivar value: the value the symbol was set to.
    :ivar line_no: the 1-based line number within that fragment.
    """

    source: str
    value: str
    line_no: int

    def format(self) -> str:
        return f"{self.source} (line {self.line_no}): ={self.value}"


@dataclass
class ExplainReport:
    """The outcome of explaining a symbol.

    Attributes:
        symbol: The symbol that was queried.
        assignments: Every fragment (in the query context) that set the symbol,
            sorted by source then line number.
        final_value: The symbol's final merged value, or ``None`` when no
            fragment set it.
        final_source: The fragment that supplied the final value, or ``None``.
        profile: The profile the query was scoped to, or ``None``.
        error: A usage-error message (missing/unknown profile, unresolved
            fragment), or ``None`` when the query is answerable.
    """

    symbol: str
    assignments: tuple[Assignment, ...] = ()
    final_value: str | None = None
    final_source: str | None = None
    profile: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """``True`` when the query is answerable (no usage error).

        A symbol that no fragment sets is still answerable (``ok``) — it simply
        has no assignments and no final value.
        """
        return self.error is None

    @property
    def exit_code(self) -> int:
        """Process exit code: 0 when answerable, 2 on usage error."""
        return 0 if self.ok else 2


def _collect(
    order: tuple[str, ...],
    library: dict[str, Fragment],
    symbol: str,
) -> tuple[Assignment, ...]:
    """Collect the fragments in *order* that set *symbol*, sorted by source."""
    assignments: list[Assignment] = []
    for name in order:
        key = _resolve_name(name, library)
        if key is None:
            continue
        fragment = library[key]
        entry = fragment.values.get(symbol)
        if entry is not None:
            assignments.append(
                Assignment(
                    source=key,
                    value=entry.value,
                    line_no=entry.line_no,
                )
            )
    return tuple(sorted(assignments, key=lambda a: (a.source, a.line_no)))


def _final_value(
    order: tuple[str, ...],
    library: dict[str, Fragment],
    symbol: str,
) -> tuple[str | None, str | None]:
    """Return the later-wins final value of *symbol* over *order*.

    Fragments are walked in *order*; the last fragment to set *symbol* wins.
    Returns ``(value, source)`` — both ``None`` when nothing sets it.
    """
    value: str | None = None
    source: str | None = None
    for name in order:
        key = _resolve_name(name, library)
        if key is None:
            continue
        fragment = library[key]
        entry = fragment.values.get(symbol)
        if entry is not None:
            value = entry.value
            source = key
    return value, source


def explain_directory(
    directory: Path | str,
    symbol: str,
    profile: str | None = None,
) -> ExplainReport:
    """Explain *symbol* across the ``*.fragment`` files in *directory*.

    Parameters:
        directory: Directory holding the ``*.fragment`` (and optional
            ``*.profile``) files.
        symbol: The ``CONFIG_`` symbol to explain.
        profile: When given, scope the merge to this profile's fragment order;
            the final value is then that profile's later-wins value.

    Returns a :class:`ExplainReport`. A missing/unknown profile (or a profile
    referencing an unresolved fragment) is a usage error (``ok`` is ``False``,
    exit code 2) — the same :func:`compose_directory` behaviour.
    """
    directory = Path(directory)
    library = {f.name: f for f in load_directory(directory)}

    if profile:
        compose: ComposeReport = compose_directory(directory, profile)
        if not compose.ok:
            return ExplainReport(
                symbol=symbol,
                profile=profile,
                error=render_error(compose),
            )
        order = compose.order
        final_value, final_source = _final_value(order, library, symbol)
        assignments = _collect(order, library, symbol)
        return ExplainReport(
            symbol=symbol,
            assignments=assignments,
            final_value=final_value,
            final_source=final_source,
            profile=profile,
        )

    order = tuple(sorted(library))
    final_value, final_source = _final_value(order, library, symbol)
    assignments = _collect(order, library, symbol)
    return ExplainReport(
        symbol=symbol,
        assignments=assignments,
        final_value=final_value,
        final_source=final_source,
    )


def render_explain(report: ExplainReport) -> str:
    """Render an :class:`ExplainReport` as the ``explain`` command prints."""
    header = (
        f"{report.symbol} (profile {report.profile!r}):"
        if report.profile
        else f"{report.symbol}:"
    )
    if not report.assignments:
        return "\n".join([header, "  not set by any fragment in the library"])
    lines = [
        header,
        f"  set by {len(report.assignments)} fragment(s):",
    ]
    lines.extend(f"    {a.format()}" for a in report.assignments)
    lines.append(
        f"  final value: ={report.final_value} (from {report.final_source})"
    )
    return "\n".join(lines)
