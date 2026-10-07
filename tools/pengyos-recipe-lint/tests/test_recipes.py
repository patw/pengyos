"""Tests for recipe discovery (``pengyos_recipe_lint.recipes``)."""

from __future__ import annotations

import pytest

from pengyos_recipe_lint.recipes import Recipe, discover_recipe, discover_recipes


def _by_name(recipes: list[Recipe], name: str) -> Recipe:
    return next(r for r in recipes if r.name == name)


def test_discover_finds_all_recipes_sorted(recipe_tree):
    recipes = discover_recipes(recipe_tree)
    # 3 .mk files: good.mk, sub/bare.mk, nested/deep.mk
    assert [r.name for r in recipes] == ["good", "deep", "bare"]
    # Sorted by absolute path.
    paths = [str(r.path) for r in recipes]
    assert paths == sorted(paths)


def test_hash_sibling_resolution(recipe_tree):
    recipes = {r.name: r for r in discover_recipes(recipe_tree)}
    assert recipes["good"].hash_path is not None and recipes["good"].hash_path.name == "good.hash"
    assert recipes["bare"].hash_path is None
    assert recipes["deep"].hash_path is not None and recipes["deep"].hash_path.name == "deep.hash"


def test_recipe_name_strips_suffix(recipe_tree):
    bare = _by_name(discover_recipes(recipe_tree), "bare")
    assert bare.name == "bare"


def test_to_dict_shape(recipe_tree):
    good = discover_recipes(recipe_tree)[0]
    payload = good.to_dict()
    assert payload == {
        "name": "good",
        "path": str(good.path),
        "hash": str(good.hash_path),
    }


def test_missing_root_raises():
    with pytest.raises(FileNotFoundError):
        discover_recipes("/nope/does/not/exist")


def test_file_as_root_raises():
    with pytest.raises(NotADirectoryError):
        discover_recipes(__file__)


def test_discover_recipe_validates_suffix():
    with pytest.raises(ValueError):
        discover_recipe(__file__)


def test_discover_recipe_missing():
    with pytest.raises(FileNotFoundError):
        discover_recipe("/nope/does/not/exist.mk")


def test_discover_recipe_returns_expected(tmp_path):
    mk = tmp_path / "foo.mk"
    mk.write_text("", encoding="utf-8")
    recipe = discover_recipe(mk)
    assert isinstance(recipe, Recipe)
    assert recipe.name == "foo"
    assert recipe.hash_path is None
