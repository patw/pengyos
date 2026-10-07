"""Discovery of Buildroot recipe pairs on disk.

A *recipe* is a ``.mk`` file. Its optional *hash sibling* is the ``.hash`` file
with the same stem in the same directory (e.g. ``foo.mk`` -> ``foo.hash``).

This module implements the filesystem primitive everything else builds on. It
deliberately does **not** parse recipe contents — that is the parser subtask's
job. Here we only answer "what recipes exist, and where are their hashes".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

RECIPE_SUFFIX = ".mk"
HASH_SUFFIX = ".hash"


@dataclass(frozen=True)
class Recipe:
    """A single ``.mk`` recipe and the location of its hash sibling, if any."""

    path: Path
    """Absolute path to the ``.mk`` recipe file."""

    @property
    def name(self) -> str:
        """Recipe name: the ``.mk`` filename without its suffix."""
        return self.path.name[: -len(RECIPE_SUFFIX)]

    @property
    def hash_path(self) -> Path | None:
        """Path to the ``.hash`` sibling, or ``None`` if it does not exist."""
        candidate = self.path.with_suffix(HASH_SUFFIX)
        return candidate if candidate.exists() else None

    def to_dict(self) -> dict[str, str]:
        """JSON-serialisable view used by the CLI's ``--json`` output."""
        return {
            "name": self.name,
            "path": str(self.path),
            "hash": str(self.hash_path) if self.hash_path is not None else None,
        }


def discover_recipes(root: str | Path) -> list[Recipe]:
    """Return every ``.mk`` recipe under *root*, sorted by path.

    The search is deterministic (sorted) and recursive. Symlinks are not
    followed into directories to avoid cycles; a symlinked ``.mk`` file itself
    is still reported.
    """
    root_path = Path(root).resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"recipe root does not exist: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"recipe root is not a directory: {root_path}")

    recipes: list[Recipe] = []
    for path in _iter_recipe_files(root_path):
        recipes.append(Recipe(path=path))
    recipes.sort(key=lambda r: r.path)
    return recipes


def _iter_recipe_files(root: Path) -> Iterator[Path]:
    """Yield ``.mk`` files under *root* in sorted order without following dirs.

    ``Path.rglob`` follows the tree but does not descend into symlinked
    directories, which keeps discovery acyclic and deterministic.
    """
    yield from sorted(root.rglob(f"*{RECIPE_SUFFIX}"))


def discover_recipe(path: str | Path) -> Recipe:
    """Return the single :class:`Recipe` at *path* (must be a ``.mk`` file)."""
    resolved = Path(path).resolve()
    if resolved.suffix != RECIPE_SUFFIX:
        raise ValueError(f"expected a {RECIPE_SUFFIX} recipe, got: {resolved}")
    if not resolved.is_file():
        raise FileNotFoundError(f"recipe file does not exist: {resolved}")
    return Recipe(path=resolved)
