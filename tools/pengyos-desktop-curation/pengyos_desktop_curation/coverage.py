"""Coverage report against the US-DSK-2 "everyday apps covered" list.

``coverage`` reports gaps against the
US-DSK-2 everyday list. A missing everyday category is a **reported gap**, not
a crash — the tool never chokes on incomplete data, it reports it. A set that
covers every everyday category passes.

The everyday list (:data:`pengyos_desktop_curation.data.EVERYDAY_CATEGORIES`)
is the acceptance horizon the tool checks against (spec: US-DSK-2 / US-DSK-5).
The report is pure, deterministic logic over a
:class:`pengyos_desktop_curation.data.CuratedSet` — no I/O — so it is easy to
unit-test with fixtures.

Standard library only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

from .data import EVERYDAY_CATEGORIES, CuratedEntry, CuratedSet


@dataclass(frozen=True)
class CategoryStatus:
    """Whether one everyday category is covered, and by what.

    ``covered`` is True iff at least one curated entry lives in ``category``.
    ``entries`` lists the covering entries (source order); ``apps`` is their
    short name list.
    """

    category: str
    covered: bool
    entries: tuple[CuratedEntry, ...] = field(default_factory=tuple)

    @property
    def apps(self) -> tuple[str, ...]:
        """The short app names covering this category (source order)."""

        return tuple(entry.app for entry in self.entries)

    def as_dict(self) -> dict[str, object]:
        """Serialise as a plain dict, in a stable key order."""

        return {
            "category": self.category,
            "covered": self.covered,
            "apps": list(self.apps),
        }

    def to_text(self) -> str:
        """One human-readable line for this category.

        Covered categories show the covering apps; gaps show ``MISSING``.
        """

        marker = "✓" if self.covered else "✗"
        detail = ", ".join(self.apps) if self.covered else "MISSING"
        return f"  {marker} {self.category:<11} {detail}"


@dataclass(frozen=True)
class CoverageReport:
    """The coverage verdict over the everyday list.

    ``status`` is one :class:`CategoryStatus` per everyday category, in the
    everyday-list order. ``gaps`` is the list of everyday categories with no
    covering entry; ``is_complete`` is True iff there are no gaps.
    """

    everyday: tuple[str, ...]
    status: tuple[CategoryStatus, ...]

    @property
    def gaps(self) -> tuple[str, ...]:
        """Everyday categories with no covering curated entry."""

        return tuple(s.category for s in self.status if not s.covered)

    @property
    def is_complete(self) -> bool:
        """Whether every everyday category is covered."""

        return not self.gaps

    @property
    def covered_count(self) -> int:
        """How many everyday categories are covered."""

        return sum(1 for s in self.status if s.covered)

    @property
    def total_count(self) -> int:
        """How many everyday categories there are in total."""

        return len(self.status)

    def as_dict(self) -> dict[str, object]:
        """Serialise as a plain dict, in a stable key order.

        Deterministic (``sort_keys`` on emit) so two runs against the same data
        are byte-identical.
        """

        return {
            "complete": self.is_complete,
            "everyday_count": self.total_count,
            "covered_count": self.covered_count,
            "gaps": list(self.gaps),
            "categories": [s.as_dict() for s in self.status],
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialise to deterministic JSON (sorted keys)."""

        return json.dumps(
            self.as_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    def to_text(self) -> str:
        """One human-readable report.

        The final line states the verdict. A complete set prints a pass line;
        a gap set prints a fail line naming the gaps.
        """

        lines = [f"Coverage against the US-DSK-2 everyday list ({self.total_count} categories)"]
        lines.extend(status.to_text() for status in self.status)
        if self.is_complete:
            lines.append(f"✓ complete: {self.covered_count}/{self.total_count} everyday categories covered")
        else:
            lines.append(
                f"✗ {len(self.gaps)} gap(s): {', '.join(self.gaps)} — "
                f"{self.covered_count}/{self.total_count} everyday categories covered"
            )
        return "\n".join(lines)


def build_report(
    curated: CuratedSet,
    everyday: Iterable[str] = EVERYDAY_CATEGORIES,
) -> CoverageReport:
    """Build a :class:`CoverageReport` for ``curated`` against ``everyday``.

    ``everyday`` defaults to :data:`EVERYDAY_CATEGORIES`. The report has one
    :class:`CategoryStatus` per everyday category, in that order; a category is
    covered iff at least one curated entry lives in it.
    """

    everyday = tuple(everyday)
    status: list[CategoryStatus] = []
    for category in everyday:
        entries = tuple(curated.by_category(category))
        status.append(
            CategoryStatus(
                category=category,
                covered=bool(entries),
                entries=entries,
            )
        )
    return CoverageReport(everyday=everyday, status=tuple(status))
