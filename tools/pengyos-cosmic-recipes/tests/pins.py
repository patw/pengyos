"""Shared test helper: the COSMIC pins the shipping path actually builds from.

`scripts/cosmic-components.tsv` is what `scripts/build-cosmic.sh` clones and
builds today. `cosmic.toml` is the recipes lane's own manifest. Two pin sets for
one component set is a divergence hazard (docs/cosmic-epoch-1.9.md), so the tests
compare them instead of each asserting its own literal SHAs - a literal-SHA test
silently goes stale the moment a pin moves, and worse, it can pass while the two
sets disagree.
"""
from __future__ import annotations

from pathlib import Path

# tests/ -> tools/pengyos-cosmic-recipes -> tools -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
TSV = REPO_ROOT / "scripts" / "cosmic-components.tsv"


def shipping_pins(path: Path = TSV) -> dict[str, str]:
    """component name -> pinned commit, from the shipping component list."""
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) >= 3:
            pins[cols[0]] = cols[2]
    return pins
