"""Classify each COSMIC native dep as shipped or recipe-needed.

The single highest-signal question this package answers before authoring a
recipe: *does the pinned Buildroot 2026.08 already ship this dependency?*

Each candidate (see :mod:`.catalogue`) maps to one or more kconfig symbols. A
candidate is classified against the vendored symbol list:

* ``shipped`` — 2026.08 already provides it (**every** symbol it enables is
  present), so PengyOS gets it free and **no recipe is authored**;
* ``recipe_needed`` — at least one symbol it enables is **absent** from 2026.08,
  so PengyOS must author a pinned, hashed BR2_EXTERNAL recipe.

The multi-symbol rule matters for the ``fonts`` candidate: it enables ten font
families, and if *any* of them is missing PengyOS still needs a recipe for it —
so "absent" is decided per-symbol and "shipped" requires *all* present.

The result is recorded as committed data (``data/classification.json``) so the
classification is reproducible and reviewable without running the code.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from . import catalogue
from .symbols import SymbolList, default_symbol_list_path


class Status(str, Enum):
    """The classification outcome for a candidate."""

    SHIPPED = "shipped"
    RECIPE_NEEDED = "recipe_needed"


@dataclass(frozen=True)
class Classification:
    """The classification of one candidate against the symbol list."""

    name: str
    status: Status
    br_symbols: tuple[str, ...]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable view (used by ``classify --json`` + the data file)."""
        return {
            "name": self.name,
            "status": self.status.value,
            "br_symbols": list(self.br_symbols),
            "reason": self.reason,
        }


@dataclass
class ClassificationReport:
    """The full classification of every candidate."""

    tag: str
    source: str
    entries: list[Classification] = field(default_factory=list)

    @property
    def recipe_needed(self) -> list[Classification]:
        """Candidates PengyOS must author a recipe for (in declaration order)."""
        return [entry for entry in self.entries if entry.status == Status.RECIPE_NEEDED]

    @property
    def shipped(self) -> list[Classification]:
        """Candidates 2026.08 already provides (in declaration order)."""
        return [entry for entry in self.entries if entry.status == Status.SHIPPED]

    def as_dict(self) -> dict[str, Any]:
        """JSON-serialisable view of the whole report."""
        return {
            "buildroot_tag": self.tag,
            "source": self.source,
            "classification": [entry.as_dict() for entry in self.entries],
        }


def _classify_one(candidate: catalogue.Candidate, symbols: SymbolList) -> Classification:
    """Classify a single *candidate* against *symbols*."""
    absent = [sym for sym in candidate.br_symbols if not symbols.contains(sym)]
    present = [sym for sym in candidate.br_symbols if symbols.contains(sym)]
    if absent:
        reason = (
            f"{len(absent)} of {len(candidate.br_symbols)} symbol(s) absent from "
            f"2026.08: {', '.join(absent)}"
        )
        status = Status.RECIPE_NEEDED
    else:
        reason = (
            f"all {len(present)} symbol(s) present in 2026.08: "
            f"{', '.join(present)}"
        )
        status = Status.SHIPPED
    return Classification(
        name=candidate.name,
        status=status,
        br_symbols=candidate.br_symbols,
        reason=reason,
    )


def classify(symbols: SymbolList) -> ClassificationReport:
    """Classify every candidate against *symbols* (in declaration order)."""
    entries = [_classify_one(candidate, symbols) for candidate in catalogue.candidates()]
    return ClassificationReport(tag=symbols.tag, source=symbols.source, entries=entries)


def classify_from_path(path: str | Path | None = None) -> ClassificationReport:
    """Load the symbol list at *path* (default: the committed snapshot) and classify.

    Passing ``None`` uses :func:`.symbols.default_symbol_list_path`, the committed
    ``scripts/tests/fixtures/buildroot-2026.08-symbols.txt``.
    """
    resolved: Path = Path(path) if path is not None else default_symbol_list_path()
    return classify(SymbolList.load(resolved))


def write_report(report: ClassificationReport, path: str | Path) -> Path:
    """Write *report* to *path* as deterministic JSON (sorted keys, trailing newline).

    Returns the path written, so callers (and the CLI) can report it.
    """
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return resolved
