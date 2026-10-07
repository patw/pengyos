"""Gate: the committed BR2_EXTERNAL recipes are in sync with the emit output.

The ``emit`` step renders each recipe in the catalogue to a
``package/<dep>/{Config.in,<dep>.mk,<dep>.hash}`` trio plus
``fragments/cosmic-native.fragment``. Those rendered files are **committed** to
this repo (the repo root *is* the BR2_EXTERNAL tree), so they are the versioned
artifact the PengyOS build actually consumes — not just something ``emit``
produces into a throwaway temp dir.

This test pins that invariant: it re-emits the committed catalogue into a temp
tree and asserts the committed ``package/`` + ``fragments/`` are *byte-identical*
to the freshly emitted output. If a recipe is hand-edited after emit (or emit is
changed without re-committing), the gate fails — the committed recipes must
always be a faithful rendering of the catalogue.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pengyos_cosmic_native_deps.emit as emit
from pengyos_cosmic_native_deps.recipes import default_recipe_catalogue

# The repo root *is* the BR2_EXTERNAL tree: the committed recipes live under its
# own ``package/`` + ``fragments/`` directories.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_COMMITTED_DIR = _REPO_ROOT / "package"
_COMMITTED_FRAGMENT = _REPO_ROOT / "fragments" / "cosmic-native.fragment"


def _committed_files(root: Path) -> dict[str, bytes]:
    """Every committed file under *root*, keyed by its path relative to *root*."""
    return {
        str(rel.relative_to(root)): rel.read_bytes()
        for rel in sorted(root.rglob("*"))
        if rel.is_file()
    }


def test_committed_recipes_match_the_emitted_output():
    """The committed recipes are byte-identical to a fresh emit run."""
    catalogue = default_recipe_catalogue()

    with tempfile.TemporaryDirectory() as tmp:
        report = emit.emit(catalogue, tmp)
        emitted = Path(tmp)

        # Every emitted file must exist in the committed tree with the same bytes.
        emitted_files = _committed_files(emitted / "package")
        emitted_files["fragments/cosmic-native.fragment"] = (
            (emitted / "fragments" / "cosmic-native.fragment").read_bytes()
        )
        committed_files = _committed_files(_COMMITTED_DIR)
        committed_files["fragments/cosmic-native.fragment"] = (
            _COMMITTED_FRAGMENT.read_bytes()
        )

        assert emitted_files.keys() == committed_files.keys(), (
            f"committed set differs from emit output:\n"
            f"  only emitted: {set(emitted_files) - set(committed_files)}\n"
            f"  only committed: {set(committed_files) - set(emitted_files)}"
        )
        for path, bytes_ in emitted_files.items():
            assert committed_files[path] == bytes_, (
                f"committed {path!r} differs from emit output"
            )


def test_committed_recipes_cover_every_catalogue_recipe():
    """Each catalogue recipe has a committed dir; none is missing."""
    catalogue = default_recipe_catalogue()
    committed = _COMMITTED_DIR
    for recipe in catalogue:
        assert (committed / recipe.name).is_dir(), (
            f"missing committed recipe dir for {recipe.name!r}"
        )


def test_committed_fragment_enables_every_catalogue_symbol():
    """The committed fragment enables every symbol every recipe enables."""
    catalogue = default_recipe_catalogue()
    expected = {
        symbol
        for recipe in catalogue
        for symbol in recipe.br_symbols
    }
    text = _COMMITTED_FRAGMENT.read_text(encoding="utf-8")
    enabled = {line.split("=", 1)[0] for line in text.splitlines() if line.strip()}
    assert enabled == expected
    # Sorted + deduped, exactly one ``=y`` per line.
    lines = text.splitlines()
    assert lines == sorted(lines)
    for line in lines:
        assert line.endswith("=y")
