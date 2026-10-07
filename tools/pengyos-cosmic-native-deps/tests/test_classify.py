"""Tests for ``pengyos_cosmic_native_deps.classify``.

The classification pass answers the package's headline question — *does the
pinned 2026.08 tree already ship this dependency?* — before any recipe is
authored. These tests pin the shipped / recipe-needed rule (including the
multi-symbol rule for the ``fonts`` candidate), the report shape, the
deterministic JSON writer, and the committed ``data/classification.json``.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import pengyos_cosmic_native_deps.classify as classify
from pengyos_cosmic_native_deps.classify import (
    ClassificationReport,
    Status,
    classify,
    classify_from_path,
    write_report,
)
from pengyos_cosmic_native_deps.symbols import SymbolList

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CLASSIFICATION_PATH = DATA_DIR / "classification.json"


# --- a synthetic symbol list for rule tests ---------------------------------


def _symbols(*present: str) -> SymbolList:
    """A minimal SymbolList over the given present symbols."""
    return SymbolList(
        symbols=frozenset(present),
        tag="2026.08",
        source="test list (tag 2026.08)",
    )


# --- the shipped / recipe_needed rule ---------------------------------------


def test_absent_symbol_makes_a_candidate_recipe_needed():
    symbols_list = _symbols("BR2_PACKAGE_SEATD")  # greetd absent
    report = classify(symbols_list)
    greetd = next(e for e in report.entries if e.name == "greetd")
    assert greetd.status == Status.RECIPE_NEEDED
    assert greetd.br_symbols == ("BR2_PACKAGE_GREETD",)
    assert "BR2_PACKAGE_GREETD" in greetd.reason


def test_all_present_makes_a_candidate_shipped():
    symbols_list = _symbols("BR2_PACKAGE_GREETD", "BR2_PACKAGE_SEATD")
    report = classify(symbols_list)
    greetd = next(e for e in report.entries if e.name == "greetd")
    assert greetd.status == Status.SHIPPED
    assert "all 1 symbol(s) present" in greetd.reason


def test_multi_symbol_candidate_needs_a_recipe_if_any_symbol_absent():
    # The fonts candidate enables ten symbols. Even if nine are present, a
    # single absent family still requires a recipe.
    present = {
        "BR2_PACKAGE_FIRA_FONT",
        "BR2_PACKAGE_FIRA_MONO",
        "BR2_PACKAGE_FIRA_BRAND_BRICKS",
        "BR2_PACKAGE_FIRA_EXTRA",
        "BR2_PACKAGE_FIRA_LIGHT",
        "BR2_PACKAGE_FIRA_MONO_EXTRA",
        "BR2_PACKAGE_NOTO_FONTS_CJK",
        "BR2_PACKAGE_NOTO_FONTS_MONO",
        "BR2_PACKAGE_NOTO_FONTS_SC",
    }  # nine of the ten — NOTO_FONTS_TC is missing
    symbols_list = _symbols(*present)
    report = classify(symbols_list)
    fonts = next(e for e in report.entries if e.name == "fonts")
    assert fonts.status == Status.RECIPE_NEEDED
    assert "BR2_PACKAGE_NOTO_FONTS_TC" in fonts.reason
    assert "1 of 10" in fonts.reason


def test_multi_symbol_candidate_is_shipped_only_when_all_present():
    all_fonts = {
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
    }
    symbols_list = _symbols(*all_fonts)
    report = classify(symbols_list)
    fonts = next(e for e in report.entries if e.name == "fonts")
    assert fonts.status == Status.SHIPPED
    assert "all 10 symbol(s) present" in fonts.reason


def test_reason_mentions_every_absent_symbol():
    symbols_list = _symbols()  # nothing present
    report = classify(symbols_list)
    fonts = next(e for e in report.entries if e.name == "fonts")
    for sym in fonts.br_symbols:
        assert sym in fonts.reason


# --- the report API ---------------------------------------------------------


def test_report_exposes_recipe_needed_and_shipped_splits():
    symbols_list = _symbols(
        "BR2_PACKAGE_GREETD",  # present
        "BR2_PACKAGE_SEATD",
    )
    report = classify(symbols_list)
    names = {e.name for e in report.entries}
    recipe_needed = {e.name for e in report.recipe_needed}
    shipped = {e.name for e in report.shipped}
    # greetd present -> shipped; seatd present -> shipped; cosmic-portal/fonts
    # absent -> recipe_needed.
    assert "greetd" in shipped
    assert "cosmic-portal" in recipe_needed
    assert recipe_needed | shipped == names
    assert not (recipe_needed & shipped)  # disjoint


def test_entries_preserve_the_candidate_declaration_order():
    report = classify_from_path()
    assert [e.name for e in report.entries] == [
        "greetd",
        "xdg-desktop-portal",
        "cosmic-portal",
        "libdisplay-info",
        "seatd",
        "fonts",
    ]


def test_status_values_are_stable_strings():
    assert Status.SHIPPED.value == "shipped"
    assert Status.RECIPE_NEEDED.value == "recipe_needed"


# --- the committed data file ------------------------------------------------


def _load_committed() -> dict:
    return json.loads(CLASSIFICATION_PATH.read_text(encoding="utf-8"))


def test_committed_classification_exists():
    assert CLASSIFICATION_PATH.exists()
    assert CLASSIFICATION_PATH.stat().st_size > 0


def test_committed_classification_matches_the_live_classification():
    """The committed file must equal a fresh classification of the snapshot."""
    live = classify_from_path().as_dict()
    committed = _load_committed()
    assert committed == live


def test_committed_classification_records_the_tag_and_source():
    committed = _load_committed()
    assert committed["buildroot_tag"] == "2026.08"
    assert committed["source"] == "buildroot-2026.08-symbols.txt (tag 2026.08)"


def test_committed_classification_pins_the_recipe_needed_set():
    committed = _load_committed()
    by_name = {e["name"]: e for e in committed["classification"]}
    for name in ("greetd", "xdg-desktop-portal", "cosmic-portal", "fonts"):
        assert by_name[name]["status"] == "recipe_needed", name
    for name in ("libdisplay-info", "seatd"):
        assert by_name[name]["status"] == "shipped", name


def test_committed_classification_records_every_symbol():
    import pengyos_cosmic_native_deps.catalogue as catalogue

    committed = _load_committed()
    recorded: set[str] = set()
    for entry in committed["classification"]:
        recorded.update(entry["br_symbols"])
    # Every candidate's declared symbols are recorded verbatim.
    for cand in catalogue.candidates():
        assert set(cand.br_symbols).issubset(recorded)


# --- the deterministic JSON writer ------------------------------------------


def test_write_report_is_deterministic(tmp_path):
    report = classify_from_path()
    a = write_report(report, tmp_path / "a.json")
    b = write_report(report, tmp_path / "b.json")
    assert a.read_bytes() == b.read_bytes()
    # The writer appends a trailing newline.
    assert a.read_text(encoding="utf-8").endswith("\n")


def test_write_report_creates_parent_directories(tmp_path):
    report = classify_from_path()
    nested = tmp_path / "deep" / "nested" / "classification.json"
    write_report(report, nested)
    assert nested.exists()
    # And it round-trips as valid JSON equal to the live report.
    assert json.loads(nested.read_text(encoding="utf-8")) == classify_from_path().as_dict()
