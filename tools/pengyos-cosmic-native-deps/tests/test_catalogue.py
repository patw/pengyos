"""Tests for ``pengyos_cosmic_native_deps.catalogue``.

The catalogue is the seed of the recipe catalogue: the finite set of native
dependencies COSMIC may need. These tests pin the candidate set and its shape so
the follow-up items (classification, modelling, emit) build on a stable contract.
"""

from __future__ import annotations

import pengyos_cosmic_native_deps.catalogue as catalogue


def test_candidate_set_is_non_empty():
    cands = catalogue.candidates()
    assert cands, "the candidate set must not be empty"


def test_expected_candidates_present():
    names = {c.name for c in catalogue.candidates()}
    assert {
        "greetd",
        "xdg-desktop-portal",
        "cosmic-portal",
        "libdisplay-info",
        "seatd",
        "fonts",
    }.issubset(names)


def test_candidates_return_a_fresh_copy():
    cands = catalogue.candidates()
    cands.append("not-a-candidate")  # type: ignore[list-item]
    assert len(catalogue.candidates()) == 6  # the module-level tuple is untouched


def test_each_candidate_has_a_name_and_symbols():
    for cand in catalogue.candidates():
        assert cand.name, "candidate has no name"
        assert cand.br_symbols, f"{cand.name} has no br_symbol"
        assert all(sym.startswith("BR2_PACKAGE_") for sym in cand.br_symbols), (
            f"{cand.name} has a non-BR2_PACKAGE symbol: {cand.br_symbols}"
        )
        assert cand.description


def test_greetd_enables_its_symbol():
    greetd = next(c for c in catalogue.candidates() if c.name == "greetd")
    assert greetd.br_symbols == ("BR2_PACKAGE_GREETD",)


def test_fonts_map_to_multiple_symbols():
    fonts = next(c for c in catalogue.candidates() if c.name == "fonts")
    assert len(fonts.br_symbols) > 1
    assert any("FIRA" in s for s in fonts.br_symbols)
    assert any("NOTO" in s for s in fonts.br_symbols)


def test_dataclass_is_frozen():
    cand = catalogue.candidates()[0]
    try:
        cand.name = "hacked"  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        raise AssertionError("Candidate is not frozen")
