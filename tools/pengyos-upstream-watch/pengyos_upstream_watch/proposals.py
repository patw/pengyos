"""Proposal emission.

The OBSERVE stage decides, for each pinned component, whether the latest
upstream release has moved past the pinned version, and if so, emits a change
*proposal*. This module holds two things:

* :class:`Proposal` — the value object describing one drifted pin, with the
  fields the spec calls for: ``(component, current, available, lts?, url)``.
* :func:`detect_proposals` — the pure detection pipeline that, given the pins
  and a :class:`~pengyos_upstream_watch.fetcher.Fetcher`, walks every pin,
  classifies the drift (via :mod:`compare`) and emits a sorted proposal list.

Everything here is pure and offline-testable: :func:`detect_proposals` takes a
:class:`Fetcher` (a mock in tests) and never touches I/O itself. The only
network seam is the fetcher.

Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from .compare import Drift, classify, parse_version
from .fetcher import Component, Fetcher

__all__ = [
    "Proposal",
    "sort_by_importance",
    "detect_proposals",
    "is_lts",
]


def is_lts(version: str) -> bool:
    """Whether a pinned version is an LTS-style two-part (``x.y``) line.

    PengyOS LTS lines publish two-part versions (``1.36``) while regular lines
    publish three-part versions (``1.37.0``); :func:`is_lts` flags the former.
    A version that cannot be parsed is treated as *not* LTS.
    """
    try:
        return len(parse_version(version).core) == 2
    except ValueError:
        return False


@dataclass(frozen=True)
class Proposal:
    """One change proposal: a pin that has drifted out of date.

    Attributes
    ----------
    component:
        The pinned component name (the proposal label).
    current:
        The currently pinned version.
    available:
        The latest upstream version reported by the fetcher (newer than
        ``current`` — the whole reason the proposal exists).
    lts:
        Whether the pinned line is an LTS-style two-part version.
    url:
        The upstream URL to fetch from, if the pin carried one.
    """

    component: str
    current: str
    available: str
    lts: bool
    url: str | None

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict for the JSON proposals document."""
        return {
            "component": self.component,
            "current": self.current,
            "available": self.available,
            "lts": self.lts,
            "url": self.url,
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.component}: {self.current} -> {self.available}"


def sort_by_importance(proposals: Sequence[Proposal]) -> list[Proposal]:
    """Order proposals by importance: non-LTS first, then the largest jump.

    Importance is a total order so the emitted document is deterministic:

    1. Non-LTS lines before LTS lines (a fresh line is more urgent than an
       established one).
    2. Larger version jumps before smaller ones. "Larger" is measured by the
       most significant core position that differs and by how far apart the
       two versions are there: a change in the major core outranks a change in
       the minor core, which outranks a patch-level change.
    3. Component name ascending as a final, stable tie-breaker.

    The sort is stable, so the name tie-break is applied first and then the
    jump order is layered on top without disturbing equal-jump names.
    """
    non_lts = [p for p in proposals if not p.lts]
    lts = [p for p in proposals if p.lts]
    for group in (non_lts, lts):
        group.sort(key=lambda p: p.component)  # stable: name ascending
        group.sort(key=_jump_key, reverse=True)  # stable: bigger jump first
    return non_lts + lts


def _core_tuple(version: str) -> tuple[int, ...]:
    """The numeric core of ``version``, or ``()`` if it cannot be parsed."""
    from .compare import parse_version  # local import keeps the surface lean

    try:
        return parse_version(version).core
    except ValueError:
        return ()


def _jump_rank(available_core: tuple[int, ...], current_core: tuple[int, ...]) -> tuple[int, int]:
    """A measure of how far ``available`` is past ``current``.

    Returns ``(-position, magnitude)`` for the most significant core position
    that differs, or ``(0, 0)`` when the cores are equal. A more significant
    position (smaller index) and a larger magnitude both yield a "larger" rank,
    so a plain descending sort ranks the biggest jumps first.
    """
    width = max(len(available_core), len(current_core)) or 1
    available = available_core + (0,) * (width - len(available_core))
    current = current_core + (0,) * (width - len(current_core))
    for position in range(width):
        if available[position] != current[position]:
            return (-position, available[position] - current[position])
    return (0, 0)


def _jump_key(proposal: Proposal) -> tuple[int, int]:
    """The jump rank for sorting: how much newer ``available`` is than ``current``."""
    return _jump_rank(
        _core_tuple(proposal.available),
        _core_tuple(proposal.current),
    )


def detect_proposals(
    components: Sequence[Component],
    fetcher: Fetcher,
    *,
    lts_of=is_lts,
) -> list[Proposal]:
    """Walk every pin and emit proposals for the ones that have drifted.

    For each component the latest upstream version is fetched (raising a
    :class:`~pengyos_upstream_watch.fetcher.FetcherError` if it cannot be
    determined — a *ran-but-failed* outcome), the drift is classified, and a
    :class:`Proposal` is emitted when the latest release is newer than the pin.
    Equal or older releases produce no proposal. The returned list is sorted by
    :func:`sort_by_importance`.

    A pin with no current version cannot be compared and is skipped.
    """
    proposals: list[Proposal] = []
    for component in components:
        if not component.version:
            # Nothing to compare against — cannot detect drift.
            continue
        latest = fetcher.latest_version(component)
        if classify(component.version, latest) is not Drift.DRIFT:
            # Equal (up-to-date) or older (behind/backported): no proposal.
            continue
        proposals.append(
            Proposal(
                component=component.name,
                current=component.version,
                available=latest,
                lts=lts_of(component.version),
                url=component.url,
            )
        )
    return sort_by_importance(proposals)
