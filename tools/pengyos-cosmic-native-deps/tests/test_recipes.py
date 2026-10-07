"""Tests for ``pengyos_cosmic_native_deps.recipes`` (the recipe catalogue).

The catalogue models, as committed data (``data/recipes.json``), the concrete
recipe metadata — pinned version, upstream URL, build system, license, source
filename, and sha256 — for every dependency that PengyOS must author a BR2_EXTERNAL
recipe for. These tests pin the model, the loader/writer, the committed data, and
the catalogue's fidelity against the live classification.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import pengyos_cosmic_native_deps.recipes as recipes
from pengyos_cosmic_native_deps.classify import classify_from_path
from pengyos_cosmic_native_deps.recipes import (
    BuildSystem,
    Recipe,
    RecipeCatalogue,
    default_recipe_catalogue,
    default_recipe_catalogue_path,
    load_catalogue,
    write_catalogue,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RECIPES_PATH = DATA_DIR / "recipes.json"


# --- the model --------------------------------------------------------------


def test_build_system_enum_values():
    assert {bs.value for bs in BuildSystem} == {
        "meson",
        "autotools",
        "make",
        "cargo",
        "none",
    }
    # A str-enum member reads back as its value.
    assert BuildSystem.MESON == "meson"


def test_recipe_is_a_frozen_dataclass():
    recipe = default_recipe_catalogue()[0]
    with pytest.raises(AttributeError):
        recipe.version = "0"  # type: ignore[misc]


def test_as_dict_round_trips_through_the_model():
    recipe = default_recipe_catalogue()[0]
    data = recipe.as_dict()
    assert set(data) == {
        "name",
        "version",
        "url",
        "download_url",
        "build_system",
        "license",
        "source",
        "hash",
        "br_symbols",
    }
    rebuilt = recipes._recipe_from_dict(data)
    assert rebuilt == recipe


# --- the catalogue API ------------------------------------------------------


def test_default_recipe_catalogue_path_points_at_committed_data():
    assert default_recipe_catalogue_path() == RECIPES_PATH
    assert RECIPES_PATH.is_file()


def test_catalogue_loads_the_committed_recipes():
    catalogue = default_recipe_catalogue()
    assert len(catalogue) == 4
    assert [r.name for r in catalogue] == [
        "greetd",
        "xdg-desktop-portal",
        "cosmic-portal",
        "fonts",
    ]


def test_catalogue_iterates_and_indexes():
    catalogue = default_recipe_catalogue()
    assert catalogue[0].name == "greetd"
    assert catalogue[-1].name == "fonts"
    assert list(catalogue)[2].name == "cosmic-portal"


def test_by_name_falls_back_to_none():
    catalogue = default_recipe_catalogue()
    assert catalogue.by_name("greetd").version == "0.10.3"
    assert catalogue.by_name("not-a-real-dep") is None


def test_filter_by_names_preserves_declaration_order():
    catalogue = default_recipe_catalogue()
    filtered = catalogue.filter_by_names(["fonts", "greetd"])
    assert [r.name for r in filtered] == ["greetd", "fonts"]
    # A name not in the catalogue yields nothing.
    assert catalogue.filter_by_names(["ghost"]) == []


# --- the committed data (real, pinned values) -------------------------------


def test_greetd_is_a_cargo_package_pinned_at_0_10_3():
    greetd = default_recipe_catalogue().by_name("greetd")
    assert greetd.version == "0.10.3"
    assert greetd.build_system is BuildSystem.CARGO
    assert greetd.license == "GPL-3.0-only"
    # The GitHub archive endpoint names its tarball `$(VERSION).tar.gz`, so the
    # Buildroot `_SOURCE` (and the `.hash` key) is `0.10.3.tar.gz`.
    assert greetd.source == "0.10.3.tar.gz"
    assert greetd.br_symbols == ("BR2_PACKAGE_GREETD",)
    assert greetd.hash == "ee5cb70e0add4ca9c9fe57e47581ab0002d44c07743fb5492469f3b570db640b"


def test_xdg_desktop_portal_is_meson_lgpl():
    portal = default_recipe_catalogue().by_name("xdg-desktop-portal")
    assert portal.version == "1.22.1"
    assert portal.build_system is BuildSystem.MESON
    assert portal.license == "LGPL-2.1-only"
    assert portal.source == "1.22.1.tar.gz"
    assert portal.br_symbols == ("BR2_PACKAGE_XDG_DESKTOP_PORTAL",)


def test_download_urls_point_at_the_downloadable_github_mirrors():
    """The two gitlab archives are Cloudflare-challenged, so the catalogue
    downloads greetd / xdg-desktop-portal from their canonical GitHub mirrors.

    The GitHub archive is byte-identical to the pinned tarball (same commit,
    same sha256), so this is a valid drop-in for the GitLab `/-/archive` URL.
    """
    greetd = default_recipe_catalogue().by_name("greetd")
    portal = default_recipe_catalogue().by_name("xdg-desktop-portal")

    assert greetd.download_url == (
        "https://github.com/kennylevinsen/greetd/archive/refs/tags/0.10.3.tar.gz"
    )
    assert portal.download_url == (
        "https://github.com/flatpak/xdg-desktop-portal/archive/refs/tags/1.22.1.tar.gz"
    )
    # Neither endpoint is a gitlab host (the challenged archive path).
    assert "gitlab" not in greetd.download_url
    assert "gitlab" not in portal.download_url


def test_download_url_ends_with_the_source_filename():
    """The archive URL must resolve to the `_SOURCE` filename (Buildroot
    downloads `_SITE` + `_SOURCE`), so the emitted recipe is self-consistent."""
    for recipe in default_recipe_catalogue():
        if recipe.build_system is BuildSystem.NONE:
            continue
        assert recipe.download_url.endswith("/" + recipe.source), (
            f"{recipe.name}: {recipe.download_url} must end with {recipe.source!r}"
        )


def test_cosmic_portal_is_a_cargo_backend():
    cosmic = default_recipe_catalogue().by_name("cosmic-portal")
    assert cosmic.build_system is BuildSystem.CARGO
    assert cosmic.license == "GPL-3.0-or-later"
    # The GitHub archive names its tarball `epoch-1.8.0.tar.gz` (the download
    # URL's final path segment == the Buildroot `_SOURCE` / `.hash` key).
    assert cosmic.source == "epoch-1.8.0.tar.gz"
    assert cosmic.br_symbols == (
        "BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC",
    )


def test_fonts_are_data_only():
    fonts = default_recipe_catalogue().by_name("fonts")
    assert fonts.build_system is BuildSystem.NONE
    assert fonts.version == ""
    assert fonts.source == ""
    assert fonts.hash == ""
    assert fonts.license == "OFL-1.1"
    # The fonts enable ten families (the multi-symbol candidate).
    assert len(fonts.br_symbols) == 10


def test_every_buildable_recipe_pins_a_sha256():
    for recipe in default_recipe_catalogue():
        if recipe.build_system is BuildSystem.NONE:
            continue
        assert len(recipe.hash) == 64, f"{recipe.name} hash is not 64 hex chars"
        assert recipe.hash == recipe.hash.lower()


def test_committed_json_matches_the_live_catalogue():
    """The committed file must equal a fresh serialisation of the catalogue."""
    live = json.loads(RECIPES_PATH.read_text(encoding="utf-8"))
    expected = [recipes._recipe_to_dict(r) for r in default_recipe_catalogue()]
    assert live == expected


# --- data fidelity against the live classification --------------------------


def test_catalogue_names_match_the_recipe_needed_set():
    """The catalogue must author exactly the recipe-needed deps (no more, no less).

    A shipped dep (2026.08 already provides it) must not get a recipe, and no
    recipe may be authored for a dep that is not a COSMIC candidate.
    """
    report = classify_from_path()
    needed = {entry.name for entry in report.recipe_needed}
    authored = {r.name for r in default_recipe_catalogue()}
    assert authored == needed


def test_every_recipe_maps_to_a_candidate_with_the_same_symbols():
    import pengyos_cosmic_native_deps.catalogue as catalogue

    candidate_symbols = {
        cand.name: set(cand.br_symbols) for cand in catalogue.candidates()
    }
    for recipe in default_recipe_catalogue():
        assert recipe.name in candidate_symbols, (
            f"recipe {recipe.name!r} has no matching candidate"
        )
        assert set(recipe.br_symbols) == candidate_symbols[recipe.name]


# --- validation -------------------------------------------------------------


def _one(name: str, **overrides: object) -> dict:
    """A minimal valid recipe entry, with per-test overrides."""
    entry: dict = {
        "name": name,
        "url": "https://example.com/foo",
        "build_system": "meson",
        "license": "MIT",
        "source": "foo-1.0.tar.gz",
        "hash": "a" * 64,
        "version": "1.0",
        "br_symbols": ["BR2_FOO"],
    }
    entry.update(overrides)
    return entry


def test_load_rejects_a_non_list(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "foo"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalogue(bad)


def test_load_rejects_duplicate_names(tmp_path):
    bad = tmp_path / "dup.json"
    bad.write_text(json.dumps([_one("foo"), _one("foo")]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalogue(bad)


def test_load_rejects_a_none_recipe_that_pins_a_version(tmp_path):
    bad = tmp_path / "none-version.json"
    bad.write_text(
        json.dumps(
            [_one("fonts", build_system="none", version="1.0")]
        ),
        encoding="utf-8",
    )
    # A data-only recipe that pins a version violates the invariant.
    with pytest.raises(ValueError):
        load_catalogue(bad)


def test_load_rejects_a_non_hex_hash(tmp_path):
    bad = tmp_path / "bad-hash.json"
    bad.write_text(json.dumps([_one("foo", hash="not-a-hash")]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalogue(bad)


def test_load_rejects_a_recipe_with_no_symbols(tmp_path):
    bad = tmp_path / "no-symbols.json"
    bad.write_text(json.dumps([_one("foo", br_symbols=[])]), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalogue(bad)


def test_load_accepts_a_valid_none_recipe(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            [_one("fonts", build_system="none", version="", source="", hash="")]
        ),
        encoding="utf-8",
    )
    catalogue = load_catalogue(good)
    assert catalogue[0].build_system is BuildSystem.NONE


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_catalogue(tmp_path / "does-not-exist.json")


# --- the deterministic writer -----------------------------------------------


def test_write_catalogue_is_deterministic(tmp_path):
    catalogue = default_recipe_catalogue()
    a = write_catalogue(catalogue, tmp_path / "a.json")
    b = write_catalogue(catalogue, tmp_path / "b.json")
    assert a.read_bytes() == b.read_bytes()
    assert a.read_text(encoding="utf-8").endswith("\n")


def test_write_catalogue_omits_build_fields_for_none(tmp_path):
    catalogue = default_recipe_catalogue()
    path = write_catalogue(catalogue, tmp_path / "cat.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    fonts = next(r for r in data if r["name"] == "fonts")
    assert "version" not in fonts and "source" not in fonts and "hash" not in fonts
    greetd = next(r for r in data if r["name"] == "greetd")
    assert greetd["version"] and greetd["source"] and greetd["hash"]


def test_write_then_load_round_trips(tmp_path):
    catalogue = default_recipe_catalogue()
    path = write_catalogue(catalogue, tmp_path / "cat.json")
    reloaded = load_catalogue(path)
    assert [r.as_dict() for r in reloaded] == [
        r.as_dict() for r in catalogue
    ]
