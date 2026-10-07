"""Tests for the committed curated-data layer.

Covers the data model, the loader, and the validators:

* the committed ``curated.toml`` loads into a :class:`CuratedSet` with one entry
  per US-DSK-2 everyday category, every entry signed and rationale-bearing,
* :class:`CuratedEntry` / :class:`CuratedSet` behave (as_dict, queries, ordering),
* :func:`parse_entry` / :func:`parse_entries` validate entries (missing field,
  bad lane, flatpak without ref, bool coercion),
* :func:`load` reads TOML and JSON; :func:`to_json` is deterministic.

No network, root, display, or hardware is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_desktop_curation import data
from pengyos_desktop_curation.data import (
    CuratedEntry,
    CuratedSet,
    CuratedError,
    LoadError,
    MalformedEntryError,
    load,
    load_default,
    parse_entries,
    parse_entry,
    to_json,
)

# The committed curated set — loaded once per test module.
SET = load_default()


# --- the committed data ------------------------------------------------------


def test_curated_toml_exists(repo_root: Path) -> None:
    assert (repo_root / "curated.toml").is_file()


def test_load_default_has_one_entry_per_everyday_category() -> None:
    assert len(SET) == len(data.EVERYDAY_CATEGORIES)
    # Every everyday category is present, exactly once, in file order.
    assert SET.categories() == list(data.EVERYDAY_CATEGORIES)


def test_every_entry_has_a_rationale_and_is_human_signed() -> None:
    """The artistry domain is human-ratified: no entry is unsigned or rationale-less."""

    for entry in SET:
        assert entry.rationale.strip(), f"{entry.app} has an empty rationale"
        assert entry.is_signed, f"{entry.app} is not human_signed"


def test_every_flatpak_entry_carries_a_ref() -> None:
    for entry in SET:
        if entry.lane == "flatpak":
            assert entry.has_ref, f"{entry.app} (flatpak) has no ref"


def test_committed_entries_are_all_flatpak_refs_present() -> None:
    # The emitted ref list is exactly the flatpak-lane refs, in order.
    assert len(SET.flatpak_refs()) == sum(
        1 for e in SET if e.lane == "flatpak"
    )


# --- CuratedEntry -------------------------------------------------------------


def test_entry_as_dict_matches_fields() -> None:
    (category, app, lane, ref, rationale, human_signed) = (
        SET.entries[0].category,
        SET.entries[0].app,
        SET.entries[0].lane,
        SET.entries[0].ref,
        SET.entries[0].rationale,
        SET.entries[0].human_signed,
    )
    assert SET.entries[0].as_dict() == {
        "category": category,
        "app": app,
        "lane": lane,
        "ref": ref,
        "rationale": rationale,
        "human_signed": human_signed,
    }


def test_entry_is_immutable() -> None:
    with pytest.raises(AttributeError):
        SET.entries[0].app = "x"  # type: ignore[misc]


def test_entry_validate_passes_for_good_entry() -> None:
    assert parse_entry(
        {
            "category": "browser",
            "app": "Firefox",
            "lane": "flatpak",
            "ref": "org.mozilla.FirefoxRepo",
            "rationale": "default browser",
            "human_signed": True,
        },
        0,
    ).has_ref


def test_entry_validate_flags_missing_required_field() -> None:
    with pytest.raises(MalformedEntryError, match="app"):
        parse_entry({"category": "browser", "lane": "flatpak", "ref": "x"}, 0)


def test_entry_validate_flags_empty_required_field() -> None:
    with pytest.raises(MalformedEntryError):
        parse_entry(
            {
                "category": "browser",
                "app": "   ",
                "lane": "flatpak",
                "ref": "x",
            },
            0,
        )


def test_entry_validate_flags_bad_lane() -> None:
    with pytest.raises(MalformedEntryError, match="lane"):
        parse_entry(
            {
                "category": "browser",
                "app": "x",
                "lane": "snap",
                "ref": "x",
                "rationale": "y",
            },
            0,
        )


def test_entry_validate_flags_flatpak_without_ref() -> None:
    with pytest.raises(MalformedEntryError, match="ref"):
        parse_entry(
            {
                "category": "browser",
                "app": "x",
                "lane": "flatpak",
                "rationale": "y",
            },
            0,
        )


def test_entry_non_flatpak_needs_no_ref() -> None:
    entry = parse_entry(
        {"category": "browser", "app": "x", "lane": "appimage", "rationale": "y"},
        0,
    )
    assert entry.has_ref is False


def test_entry_human_signed_defaults_false_when_absent() -> None:
    entry = parse_entry(
        {"category": "browser", "app": "x", "lane": "appimage", "rationale": "y"},
        0,
    )
    assert entry.is_signed is False


def test_entry_human_signed_rejects_non_bool() -> None:
    with pytest.raises(MalformedEntryError, match="human_signed"):
        parse_entry(
            {
                "category": "browser",
                "app": "x",
                "lane": "appimage",
                "rationale": "y",
                "human_signed": "true",
            },
            0,
        )


# --- CuratedSet queries -------------------------------------------------------


def test_set_is_iterable_and_len() -> None:
    assert len(list(SET)) == len(SET)
    assert len(SET) == 10


def test_set_categories_are_ordered_and_deduplicated() -> None:
    sample = parse_entries(
        [
            {
                "category": "browser",
                "app": "a",
                "lane": "flatpak",
                "ref": "r1",
                "rationale": "x",
            },
            {
                "category": "browser",
                "app": "b",
                "lane": "flatpak",
                "ref": "r2",
                "rationale": "x",
            },
            {
                "category": "terminal",
                "app": "c",
                "lane": "flatpak",
                "ref": "r3",
                "rationale": "x",
            },
        ]
    )
    assert sample.categories() == ["browser", "terminal"]


def test_set_by_category() -> None:
    browser = SET.by_category("browser")
    assert len(browser) == 1
    assert browser[0].category == "browser"
    assert SET.by_category("nope") == []


def test_set_lanes() -> None:
    assert SET.lanes() == ["flatpak"]


def test_set_flatpak_refs_in_source_order() -> None:
    refs = SET.flatpak_refs()
    assert refs[0] == "org.mozilla.FirefoxRepo"
    assert refs[-1] == "org.gnome.Screenshot"


def test_set_equality() -> None:
    a = load_default()
    b = load_default()
    assert a == b
    assert a != "not a set"


# --- parse_entries / load -----------------------------------------------------


def test_parse_entries_accepts_top_level_array() -> None:
    parsed = parse_entries(
        [
            {
                "category": "browser",
                "app": "a",
                "lane": "flatpak",
                "ref": "r1",
                "rationale": "x",
            },
            {
                "category": "terminal",
                "app": "c",
                "lane": "flatpak",
                "ref": "r3",
                "rationale": "x",
            },
        ]
    )
    assert len(parsed) == 2


def test_parse_entries_accepts_entry_table() -> None:
    parsed = parse_entries(
        {"entry": [{"category": "browser", "app": "a", "lane": "flatpak", "ref": "r1", "rationale": "x"}]}
    )
    assert len(parsed) == 1


def test_parse_entries_missing_entry_key() -> None:
    with pytest.raises(MalformedEntryError, match="entry"):
        parse_entries({"nope": []})


def test_parse_entries_non_mapping_entry() -> None:
    with pytest.raises(MalformedEntryError, match="entry #1"):
        parse_entries(
            [
                {
                    "category": "browser",
                    "app": "a",
                    "lane": "flatpak",
                    "ref": "r1",
                    "rationale": "x",
                },
                "bad",
            ]
        )


def test_load_reads_json(tmp_path: Path) -> None:
    path = tmp_path / "curated.json"
    path.write_text(
        '[{"category": "browser", "app": "a", "lane": "flatpak", "ref": "r1", "rationale": "x"}]',
        encoding="utf-8",
    )
    loaded = load(path)
    assert len(loaded) == 1
    assert loaded[0].app == "a"


def test_load_missing_file_raises_load_error() -> None:
    with pytest.raises(LoadError):
        load(Path("/nonexistent/curated.toml"))


def test_load_invalid_file_raises_load_error(tmp_path: Path) -> None:
    path = tmp_path / "curated.toml"
    path.write_text("this is not = valid = toml [[[", encoding="utf-8")
    with pytest.raises(LoadError):
        load(path)


# --- to_json ------------------------------------------------------------------


def test_to_json_is_deterministic() -> None:
    a = to_json(load_default())
    b = to_json(load_default())
    assert a == b


def test_to_json_round_trips() -> None:
    import json

    text = to_json(load_default())
    parsed = json.loads(text)
    assert isinstance(parsed, list)
    assert len(parsed) == len(SET)
    assert parsed[0]["app"] == "Firefox"


# --- error hierarchy ----------------------------------------------------------


def test_error_hierarchy() -> None:
    assert issubclass(MalformedEntryError, CuratedError)
    assert issubclass(LoadError, CuratedError)
