"""The candidate native dependencies for COSMIC.

This module is the seed of the recipe catalogue: the finite set of native
(non-Rust) dependencies that PengyOS's COSMIC desktop may need. It records, for
each *candidate*, its human name and the kconfig symbol(s) it enables. The
**full catalogue model** — the concrete recipe metadata (pinned version, upstream
URL, build system, license, source filename, sha256) for each dep that actually
needs a recipe — lives in :mod:`.recipes` (committed as ``data/recipes.json``); a
candidate becomes a :class:`.recipes.Recipe` once :mod:`.classify` tags it
``recipe_needed``.

The real work of this package is a **classification** pass: each candidate is
checked against the vendored Buildroot ``2026.08`` kconfig symbol list and tagged
either

* ``shipped`` — the symbol already exists in 2026.08, so PengyOS gets it for
  free and **no recipe is authored**, or
* ``missing`` — the symbol is absent from 2026.08, so PengyOS must author a
  pinned, hashed BR2_EXTERNAL recipe.

That classification is computed against the symbol list in the follow-up item;
here each candidate simply declares the symbol it would enable (``br_symbol``),
which is what the classification pass looks up. A candidate that maps to more
than one symbol (the fonts, for instance) lists them all.
"""

from __future__ import annotations

from dataclasses import dataclass

# The kconfig symbol each candidate enables when PengyOS authors a recipe for it.
# A candidate may enable several symbols (the fonts ship as more than one
# package); the first is the "primary" symbol, the rest are additional ones the
# emitted fragment will also enable.
@dataclass(frozen=True)
class Candidate:
    """A native dependency COSMIC may need that 2026.08 might not ship."""

    name: str
    """The dependency's human name, e.g. ``greetd``."""

    br_symbols: tuple[str, ...]
    """The kconfig symbol(s) this candidate enables in a BR2_EXTERNAL tree."""

    description: str
    """A one-line description of what the dependency provides."""


# The candidate set, in the order they appear in the spec. This is the whole
# catalogue of *candidates* — the classification pass decides
# which of them actually need a recipe.
CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        name="greetd",
        br_symbols=("BR2_PACKAGE_GREETD",),
        description="Minimal, distro-independent login daemon for Wayland/X11.",
    ),
    Candidate(
        name="xdg-desktop-portal",
        br_symbols=("BR2_PACKAGE_XDG_DESKTOP_PORTAL",),
        description="Portal backend for sandboxed app sandbox/screencast/etc.",
    ),
    Candidate(
        name="cosmic-portal",
        br_symbols=("BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC",),
        description="The COSMIC xdg-desktop-portal backend.",
    ),
    Candidate(
        name="libdisplay-info",
        br_symbols=("BR2_PACKAGE_LIBDISPLAY_INFO",),
        description="EDID parsing library used by cosmic-comp.",
    ),
    Candidate(
        name="seatd",
        br_symbols=("BR2_PACKAGE_SEATD",),
        description="Seat management daemon exposing a libseat backend.",
    ),
    Candidate(
        name="fonts",
        br_symbols=(
            "BR2_PACKAGE_FIRA_BRAND_BRICKS",
            "BR2_PACKAGE_FIRA_FONT",
            "BR2_PACKAGE_FIRA_EXTRA",
            "BR2_PACKAGE_FIRA_LIGHT",
            "BR2_PACKAGE_FIRA_MONO",
            "BR2_PACKAGE_FIRA_MONO_EXTRA",
            "BR2_PACKAGE_NOTO_FONTS_CJK",
            "BR2_PACKAGE_NOTO_FONTS_MONO",
            "BR2_PACKAGE_NOTO_FONTS_SC",
            "BR2_PACKAGE_NOTO_FONTS_TC",
        ),
        description="The Fira + Noto font families COSMIC ships with.",
    ),
)


def candidates() -> list[Candidate]:
    """Return the candidate list in declaration order (a fresh copy)."""
    return list(CANDIDATES)
