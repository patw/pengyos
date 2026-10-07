"""Compose a profile's fragment list into a single merged CONFIG set.

Implements the ``compose`` command and complements
:mod:`pengyos_kernel_fragments.validate`. Where ``validate`` *asserts* that a
library is well-formed, ``compose`` *merges* a named profile's ordered list of
fragments into one deterministic ``CONFIG`` set.

Profiles
--------
A **profile** is a ``*.profile`` file that lists fragment basenames, one per
line::

    # n100: QEMU virt + systemd + Intel IGPU
    qemu
    systemd
    intel-igpu

Blank lines and ``#`` comments are ignored. Profiles live in the same directory
as the fragments (the directory passed to ``compose``); ``load_directory``
already ignores non-``*.fragment`` files, so a directory may hold both
``*.fragment`` and ``*.profile`` files.

Merge semantics
---------------
Fragments are merged in *profile order*. A symbol set by a later fragment wins
over an earlier one (later-wins). When a later fragment assigns a *different*
value to a symbol an earlier fragment already set, a :class:`ConflictWarning`
is emitted documenting the override (``compose`` proceeds with the later value
rather than aborting — that is the documented precedence rule).

The merged set is emitted deterministically, sorted by symbol name, in the same
``CONFIG_X=value`` line format the fragments use.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .fragments import ConfigEntry, Fragment, load_directory


@dataclass(frozen=True)
class ConflictWarning:
    """A symbol overridden by a later fragment in the profile (later-wins).

    :ivar symbol: the overridden :data:`CONFIG_` symbol.
    :ivar previous_value: the value the symbol had before this fragment.
    :ivar previous_source: the fragment that set the previous value.
    :ivar new_value: the overriding value.
    :ivar new_source: the fragment that supplied the overriding value.
    """

    symbol: str
    previous_value: str
    previous_source: str
    new_value: str
    new_source: str

    def format(self) -> str:
        return (
            f"conflict: {self.symbol} "
            f"{self.previous_source}={self.previous_value} overridden by "
            f"{self.new_source}={self.new_value}"
        )


@dataclass
class ComposeReport:
    """The outcome of composing a profile.

    Attributes:
        profile: The profile name that was composed.
        order: The fragment names in profile order (the merge order).
        merged: The merged CONFIG entries, sorted by symbol.
        warnings: Later-wins override warnings, in the order they fired.
        missing: Fragment names the profile referenced but could not resolve.
    """

    profile: str
    order: tuple[str, ...]
    merged: tuple[ConfigEntry, ...]
    warnings: tuple[ConflictWarning, ...] = ()
    missing: tuple[str, ...] = ()
    profile_missing: bool = False

    @property
    def ok(self) -> bool:
        """``True`` when the profile resolved every fragment it referenced.

        Both a missing profile file and an unresolved referenced fragment make
        the report ``not ok`` (exit 2).
        """
        return not self.missing and not self.profile_missing

    @property
    def exit_code(self) -> int:
        """Process exit code: 0 when the profile composed, 2 on usage error."""
        return 0 if self.ok else 2


def _load_profile(profiles_dir: Path, name: str) -> tuple[str, list[str]]:
    """Load *name* from *profiles_dir*.

    Returns ``(profile_text, fragments)`` where *fragments* is the ordered list
    of fragment basenames listed in the profile. Raises
    :class:`FileNotFoundError` if no ``<name>.profile`` exists.
    """
    path = profiles_dir / f"{name}.profile"
    text = path.read_text(encoding="utf-8")
    fragments: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fragments.append(line)
    return text, fragments


def _kind(value: str) -> str:
    """Classify a bare merged value back into a ValueKind string."""
    return value if value in ("y", "n", "m") else "s"


def _merged_entries(resolved: dict[str, tuple[str, str]]) -> tuple[ConfigEntry, ...]:
    """Build sorted :class:`ConfigEntry` rows from a resolved symbol map."""
    return tuple(
        ConfigEntry(symbol, value, _kind(value), 0, profile)
        for symbol, (value, profile) in sorted(resolved.items())
    )


def _resolve_name(name: str, library: dict[str, Fragment]) -> str | None:
    """Return the library key for *name*, tolerating a missing ``.fragment``.

    A profile may list a fragment as ``qemu`` or ``qemu.fragment``; both resolve
    to the same library entry. Returns ``None`` when neither form is present.
    """
    if name in library:
        return name
    candidate = name if name.endswith(".fragment") else f"{name}.fragment"
    return candidate if candidate in library else None


def merge_fragments(
    order: Iterable[str],
    library: dict[str, Fragment],
    profile: str,
) -> ComposeReport:
    """Merge *order* (fragment names) using *library* (name -> Fragment).

    Fragments are merged in *order* with later-wins precedence. A later fragment
    that assigns a different value to an already-set symbol yields a
    :class:`ConflictWarning`. Fragment names that are absent from *library* are
    collected into :attr:`ComposeReport.missing` (the report is then ``not ok``).

    The merged set is sorted by symbol.
    """
    order = list(order)
    resolved_names = [_resolve_name(name, library) for name in order]
    missing = [
        name
        for name, key in zip(order, resolved_names)
        if key is None
    ]

    if missing:
        # Nothing else is meaningful to report when the profile is broken.
        return ComposeReport(
            profile=profile,
            order=tuple(order),
            merged=(),
            missing=tuple(missing),
        )

    # symbol -> (value, source) as first established in profile order.
    resolved: dict[str, tuple[str, str]] = {}
    warnings: list[ConflictWarning] = []

    for name, key in zip(order, resolved_names):
        fragment = library[key]
        for symbol, entry in fragment.values.items():
            if symbol in resolved:
                prev_value, prev_source = resolved[symbol]
                if prev_value != entry.value:
                    warnings.append(
                        ConflictWarning(
                            symbol=symbol,
                            previous_value=prev_value,
                            previous_source=prev_source,
                            new_value=entry.value,
                            new_source=key,
                        )
                    )
            resolved[symbol] = (entry.value, key)

    return ComposeReport(
        profile=profile,
        order=tuple(order),
        merged=_merged_entries(resolved),
        warnings=tuple(warnings),
    )


def compose_directory(
    directory: Path | str,
    profile: str,
) -> ComposeReport:
    """Load ``*.fragment`` + ``*.profile`` from *directory* and compose *profile*.

    Fragments are loaded from *directory*; the profile is read from
    *directory*/*profile*.``profile``. A missing profile is a usage error: the
    report is ``not ok`` (exit 2) with an empty merged set.
    """
    directory = Path(directory)
    try:
        _text, order = _load_profile(directory, profile)
    except FileNotFoundError:
        return ComposeReport(
            profile=profile,
            order=(),
            merged=(),
            profile_missing=True,
        )

    library = {f.name: f for f in load_directory(directory)}
    return merge_fragments(order, library, profile)


def render_merged(report: ComposeReport) -> str:
    """Return the merged CONFIG set as text (one sorted line each)."""
    return "\n".join(str(entry) for entry in report.merged)


def render_warnings(report: ComposeReport) -> str:
    """Return the conflict warnings as text (one line each)."""
    return "\n".join(w.format() for w in report.warnings)


def render_error(report: ComposeReport) -> str | None:
    """Return the usage-error message, or ``None`` when the profile composed."""
    if report.ok:
        return None
    if report.profile_missing:
        return (
            f"error: no profile named {report.profile!r} "
            f"(<directory>/{report.profile}.profile)"
        )
    return (
        f"error: profile {report.profile!r} references unknown "
        f"fragment(s): {', '.join(report.missing)}"
    )
