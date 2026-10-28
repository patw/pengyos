"""The vendored Buildroot ``2026.08`` kconfig symbol list.

This module loads the authoritative, committed symbol list
(``scripts/tests/fixtures/buildroot-2026.08-symbols.txt``) — the same snapshot
``pengyos-m2-defconfig`` vendors from (commit ``d5180309…``, tag ``2026.08``).
It answers a single question: *does this kconfig symbol exist in the pinned
2026.08 tree?* The classification pass uses that answer to decide whether a
candidate dependency needs a recipe.

The list is a fact about the pinned Buildroot, not about this tool, so it lives
with the repo's fixtures: ``scripts/tests/test_core_userland.py`` checks every
layer symbol against the same file. One copy, two readers.

Runtime depends only on the Python standard library.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# A valid kconfig symbol: an identifier, no spaces (see ``tests/test_data.py``).
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9_]+$")

# A Buildroot release tag, as recorded in the provenance header: a dotted
# version such as ``2026.08``.
_TAG_RE = re.compile(r"tag\s+(\d[\w.]*)")


def default_symbol_list_path() -> Path:
    """The committed symbol list, resolved relative to the repo root.

    The snapshot is repo-level data — the wiring suite reads the same file — so
    it sits with the other fixtures next to the BusyBox symbol list. ``classify``
    and ``list`` work out of the box against it with no argument; ``--symbols``
    overrides it.
    """
    return (
        Path(__file__).resolve().parents[3]
        / "scripts" / "tests" / "fixtures"
        / "buildroot-2026.08-symbols.txt"
    )


@dataclass(frozen=True)
class SymbolList:
    """The set of kconfig symbols present in the pinned 2026.08 tree."""

    symbols: frozenset[str]
    """Every symbol line in the vendored list (comments/blanks excluded)."""

    tag: str
    """The Buildroot release tag recorded in the provenance header (``2026.08``)."""

    source: str
    """Human label for the provenance (file name + tag)."""

    def contains(self, symbol: str) -> bool:
        """Return ``True`` when *symbol* exists in the pinned 2026.08 tree."""
        return symbol in self.symbols

    @property
    def count(self) -> int:
        """Number of symbols in the list."""
        return len(self.symbols)

    @classmethod
    def load(cls, path: str | Path) -> SymbolList:
        """Load and parse the symbol list at *path*.

        Raises :class:`FileNotFoundError` if *path* does not exist and
        :class:`ValueError` if a content line is not a valid kconfig symbol.
        """
        resolved = Path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"symbol list not found: {resolved}")
        text = resolved.read_text(encoding="utf-8")
        symbols: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not _SYMBOL_RE.match(stripped):
                raise ValueError(f"invalid symbol line: {stripped!r}")
            symbols.append(stripped)
        tag = _parse_tag(text)
        return cls(
            symbols=frozenset(symbols),
            tag=tag,
            source=f"{resolved.name} (tag {tag})",
        )


def _parse_tag(text: str) -> str:
    """Extract the Buildroot tag from the provenance header, defaulting to ``''``."""
    for line in text.splitlines():
        if line.startswith("#") and "tag" in line:
            match = _TAG_RE.search(line)
            if match is not None:
                return match.group(1)
    return ""
