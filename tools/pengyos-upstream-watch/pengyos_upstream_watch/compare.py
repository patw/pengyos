"""Semver-ish version comparison.

This module is the *decision* core of the OBSERVE stage (SPEC §3): given a
pinned ``current`` version and the ``latest`` version reported by a
:class:`~pengyos_upstream_watch.fetcher.Fetcher`, it decides whether the pin has
drifted out of date.

It is deliberately **semver-ish**, not strict SemVer 2.0. PengyOS pins mix
release styles — LTS lines publish two-part versions (``x.y``) while regular
lines publish three-part versions (``x.y.z``) — so the comparison must compare
them on a common footing:

* ``1.2`` and ``1.2.0`` describe the *same* release core, so they compare equal.
  (This is the LTS ``x.y`` vs ``x.y.z`` case the item calls out.)
* A prerelease (``1.0.0-rc1``, ``1.0.0-beta.2``) is *older* than the stable
  release it precedes, and prereleases order among themselves per SemVer.
* Build metadata (``1.0.0+git123``) is ignored, as SemVer requires.

Everything here is **pure** — no I/O, no network — so the whole surface is
covered by unit tests with mocked fetchers.

Standard library only.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

__all__ = [
    "ParsedVersion",
    "parse_version",
    "compare",
    "is_newer",
    "Drift",
    "classify",
]


# --- Parsing ----------------------------------------------------------------


#: Matches a version-ish string end to end.
#: * optional leading ``v``/``V`` (e.g. ``v1.2.3``),
#: * a numeric ``core`` of one or more dot-separated integers,
#: * an optional ``-prerelease`` (SemVer prerelease identifiers),
#: * an optional ``+build`` metadata (ignored),
#: * surrounding whitespace.
_VERSION_RE = re.compile(
    r"""
    ^\s*[vV]?
    (?P<core>\d+(?:\.\d+)*)
    (?:-(?P<prerelease>[0-9A-Za-z.-]+))?
    (?:\+(?P<build>[0-9A-Za-z.-]+))?
    \s*$
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedVersion:
    """A version string split into its comparable parts.

    Attributes
    ----------
    raw:
        The original, unparsed string (preserved for proposals and messages).
    core:
        The numeric release core as a tuple of ints, e.g. ``1.2.3`` ->
        ``(1, 2, 3)`` and ``1.2`` -> ``(1, 2)``. The length is *not*
        normalised here; :func:`compare` pads shorter cores with zeros.
    prerelease:
        The prerelease identifiers as a tuple, each an ``int`` when the
        identifier is purely numeric (so ``1.0.0-rc2`` > ``1.0.0-rc1``) and a
        ``str`` otherwise. Empty when the release is stable.
    """

    raw: str
    core: tuple[int, ...]
    prerelease: tuple[int | str, ...]

    def is_prerelease(self) -> bool:
        """Whether this is a prerelease (as opposed to a stable release)."""
        return bool(self.prerelease)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.raw


def _parse_prerelease(text: str | None) -> tuple[int | str, ...]:
    """Split a SemVer prerelease string into typed identifiers.

    ``"rc1"`` -> ``("rc", 1)`` (SemVer splits a run of digits out of an
    alphanumeric identifier); ``"alpha.2"`` -> ``("alpha", 2)``; ``None`` or
    ``""`` -> ``()`` (a stable release).
    """
    if not text:
        return ()
    ids: list[int | str] = []
    for part in text.split("."):
        if not part:
            # A stray dot (e.g. a trailing "-.") is tolerated as an empty
            # identifier, which sorts below any real identifier.
            ids.append("")
        elif part.isdigit():
            ids.append(int(part))
        else:
            # Split a trailing numeric run out of an alphanumeric identifier so
            # that "rc2" outranks "rc1" the way SemVer intends.
            m = re.match(r"^([A-Za-z]+)(\d+)$", part)
            if m:
                ids.append(m.group(1))
                ids.append(int(m.group(2)))
            else:
                ids.append(part)
    return tuple(ids)


def parse_version(text: str) -> ParsedVersion:
    """Parse a version-ish string into a :class:`ParsedVersion`.

    Raises :class:`ValueError` when ``text`` has no numeric core (e.g. an empty
    string or ``"latest"``). A leading ``v``/``V`` is accepted and stripped.
    """
    if text is None:
        raise ValueError("cannot parse a None version")
    raw = text.strip()
    match = _VERSION_RE.match(raw)
    if not match:
        raise ValueError(f"not a version: {text!r}")
    core = tuple(int(part) for part in match.group("core").split("."))
    prerelease = _parse_prerelease(match.group("prerelease"))
    return ParsedVersion(raw=raw, core=core, prerelease=prerelease)


# --- Comparison -------------------------------------------------------------


def _pad(cores: list[tuple[int, ...]]) -> list[tuple[int, ...]]:
    """Pad every core with trailing zeros up to the longest core's length."""
    width = max(len(c) for c in cores) or 1
    return [c + (0,) * (width - len(c)) for c in cores]


def _compare_prerelease(a: tuple[int | str, ...], b: tuple[int | str, ...]) -> int:
    """Compare two prerelease identifier tuples, SemVer-style.

    Stable (empty) outranks any prerelease. A longer identifier list outranks a
    shorter one when the shared prefix is equal; a numeric identifier ranks
    below a non-numeric one (SemVer).
    """
    # A stable release (no prerelease) is newer than any prerelease.
    if not a and not b:
        return 0
    if not a:
        return 1
    if not b:
        return -1
    for x, y in zip(a, b):
        if x == y:
            continue
        both_int = isinstance(x, int) and isinstance(y, int)
        if both_int:
            return -1 if x < y else 1
        # SemVer: a numeric identifier has lower precedence than a non-numeric
        # one, so "1.0.0-1" ranks below "1.0.0-alpha".
        if isinstance(x, int):
            return -1
        if isinstance(y, int):
            return 1
        return -1 if x < y else 1
    # All shared identifiers equal: the longer prerelease outranks the shorter.
    return -1 if len(a) < len(b) else (1 if len(a) > len(b) else 0)


def compare(a: str, b: str) -> int:
    """Compare two version strings; return ``-1``, ``0`` or ``1``.

    ``compare(a, b) < 0`` means ``a`` is older than ``b``; ``0`` means equal;
    ``> 0`` means ``a`` is newer. ``x.y`` and ``x.y.z`` compare equal (the LTS
    equivalence), and prereleases rank below their stable release.
    """
    pa = parse_version(a)
    pb = parse_version(b)
    pa_core, pb_core = _pad([pa.core, pb.core])
    if pa_core < pb_core:
        return -1
    if pa_core > pb_core:
        return 1
    return _compare_prerelease(pa.prerelease, pb.prerelease)


def is_newer(current: str, latest: str) -> bool:
    """True when ``latest`` is strictly newer than ``current``."""
    return compare(latest, current) > 0


# --- Drift classification ---------------------------------------------------


class Drift(enum.Enum):
    """The relationship between a pinned current version and the latest release."""

    UP_TO_DATE = "up-to-date"
    """The latest release equals the current pin (no drift)."""
    BEHIND = "behind"
    """The latest release is *older* than the current pin (backported build)."""
    DRIFT = "drift"
    """The latest release is newer than the current pin — a change proposal."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


def classify(current: str, latest: str) -> Drift:
    """Classify the drift between ``current`` and ``latest``."""
    sign = compare(latest, current)
    if sign > 0:
        return Drift.DRIFT
    if sign < 0:
        return Drift.BEHIND
    return Drift.UP_TO_DATE
