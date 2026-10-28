"""Tests for ``pengyos_cosmic_native_deps.symbols``.

The symbol list is the source of truth for the classification pass: *does this
kconfig symbol exist in the pinned 2026.08 tree?* These tests pin the loader's
behaviour — existence, valid-symbol integrity, tag provenance, and the clean
errors on a missing or malformed list.
"""

from __future__ import annotations

import pathlib

import pytest

import pengyos_cosmic_native_deps.symbols as symbols

ROOT = pathlib.Path(__file__).resolve().parent.parent
SYMBOLS_PATH = ROOT.parents[1] / "scripts" / "tests" / "fixtures" / "buildroot-2026.08-symbols.txt"


def test_default_symbol_list_path_points_at_the_committed_snapshot():
    path = symbols.default_symbol_list_path()
    assert path == SYMBOLS_PATH
    assert path.is_file(), "the committed symbol list must be loadable out of the box"


def test_load_reads_the_committed_list():
    loaded = symbols.SymbolList.load(SYMBOLS_PATH)
    assert loaded.count > 0
    # A known-good symbol from the authoritative snapshot.
    assert loaded.contains("BR2_TARGET_GRUB2")


def test_load_is_case_sensitive_to_the_symbol_name():
    loaded = symbols.SymbolList.load(SYMBOLS_PATH)
    assert loaded.contains("BR2_PACKAGE_SEATD")
    # kconfig symbols are upper-case identifiers; the lowercase form is absent.
    assert not loaded.contains("br2_package_seatd")


def test_tag_is_parsed_from_the_provenance_header():
    loaded = symbols.SymbolList.load(SYMBOLS_PATH)
    assert loaded.tag == "2026.08"


def test_source_records_the_provenance_label():
    loaded = symbols.SymbolList.load(SYMBOLS_PATH)
    assert loaded.source == "buildroot-2026.08-symbols.txt (tag 2026.08)"


def test_load_rejects_a_missing_file():
    with pytest.raises(FileNotFoundError):
        symbols.SymbolList.load(ROOT / "data" / "does-not-exist.txt")


def test_load_rejects_an_invalid_symbol_line(tmp_path):
    bad = tmp_path / "bad.txt"
    bad.write_text("# header\nBR2_OK\nBR2 BAD-CHAR\n", encoding="utf-8")
    with pytest.raises(ValueError):
        symbols.SymbolList.load(bad)


def test_load_skips_comments_and_blank_lines(tmp_path):
    clean = tmp_path / "clean.txt"
    clean.write_text(
        "# a comment\n\nBR2_A\n  \nBR2_B\n# trailing comment\n",
        encoding="utf-8",
    )
    loaded = symbols.SymbolList.load(clean)
    assert loaded.symbols == frozenset({"BR2_A", "BR2_B"})
    assert loaded.count == 2


def test_load_without_a_tag_header_defaults_to_empty_string(tmp_path):
    no_tag = tmp_path / "notag.txt"
    no_tag.write_text("BR2_A\nBR2_B\n", encoding="utf-8")
    loaded = symbols.SymbolList.load(no_tag)
    assert loaded.tag == ""
