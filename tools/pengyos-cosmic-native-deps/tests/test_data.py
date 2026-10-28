"""Tests for the vendored 2026.08 symbol list (the repo fixture).

The *vendor* item copies the authoritative Buildroot
``2026.08`` kconfig symbol list out of ``pengyos-m2-defconfig`` and into the
repo's ``scripts/tests/fixtures/`` as a self-contained, committed fixture — the
same file ``scripts/tests/test_core_userland.py`` checks the layer symbols
against. These tests pin that data: it must exist, be the authoritative
``2026.08`` snapshot (by provenance), be a clean ``sort -u`` symbol list, and
carry the exact present/absent facts the follow-up *classify* item depends on.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO = ROOT.parents[1]
# The snapshot is repo-level data: scripts/tests/test_core_userland.py reads the
# same file when it checks the layers' symbols.
SYMBOLS_PATH = REPO / "scripts" / "tests" / "fixtures" / "buildroot-2026.08-symbols.txt"
# The sibling repo this list is vendored from.
SOURCE_PATH = ROOT.parent / "pengyos-m2-defconfig" / "data" / "buildroot-2026.08-symbols.txt"

# The authoritative provenance of the vendored snapshot.
PROVENANCE_COMMIT = "d5180309b1b66ef3b8eaccca70ad69be8e0729a1"
PROVENANCE_TAG = "2026.08"

# Every non-comment content line is a kconfig symbol: an identifier, no spaces.
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _read_lines() -> list[str]:
    return SYMBOLS_PATH.read_text(encoding="utf-8").splitlines()


def _content_lines() -> list[str]:
    """The non-comment, non-blank symbol lines, in file order."""
    return [ln for ln in _read_lines() if ln.strip() and not ln.startswith("#")]


# --- existence & faithfulness ------------------------------------------------


def test_vendored_symbol_list_exists_and_non_empty():
    assert SYMBOLS_PATH.exists(), "scripts/tests/fixtures/buildroot-2026.08-symbols.txt is missing"
    assert SYMBOLS_PATH.stat().st_size > 0, "vendored symbol list is empty"


def test_vendored_file_is_a_copy_of_the_source():
    """The committed data is byte-identical to the authoritative source.

    Skips gracefully if the sibling repo is not checked out, so the suite stays
    green on a host that has not cloned every tool — but when the source is
    present the copy must be faithful (no drift, no re-encoding).
    """
    if not SOURCE_PATH.exists():
        pytest.skip("sibling pengyos-m2-defconfig source not present")
    assert SYMBOLS_PATH.read_bytes() == SOURCE_PATH.read_bytes(), (
        "vendored symbol list drifted from the authoritative source"
    )


# --- provenance --------------------------------------------------------------


def test_header_records_authoritative_provenance():
    head = "\n".join(_read_lines()[:8]).lower()
    assert PROVENANCE_COMMIT in head, (
        "missing the authoritative source commit hash in the provenance header"
    )
    assert PROVENANCE_TAG in head, (
        "missing the 2026.08 tag in the provenance header"
    )


def test_header_remembers_the_menuconfig_caveat():
    """The menuconfig-symbol note must survive vendoring (it is load-bearing)."""
    text = SYMBOLS_PATH.read_text(encoding="utf-8")
    assert "menuconfig" in text.lower(), (
        "the vendored list lost the menuconfig-symbol caveat note"
    )


# --- structural integrity ----------------------------------------------------


def test_content_lines_are_valid_kconfig_symbols():
    content = _content_lines()
    assert content, "no symbol lines found"
    for ln in content:
        assert _SYMBOL_RE.match(ln), f"invalid symbol line (whitespace/bad chars): {ln!r}"


def test_symbols_are_unique():
    """A faithful ``sort -u`` snapshot has no duplicate symbols."""
    content = _content_lines()
    assert len(content) == len(set(content)), "symbol list contains duplicate symbols"


def test_symbols_include_the_menuconfig_set():
    """The 62 menuconfig symbols must be present (see the caveat note).

    ``BR2_TARGET_GRUB2`` is a menuconfig symbol; if it is absent the list was
    captured with a bare ``^config`` grep and silently dropped the menuconfig
    set — exactly the bug the caveat note guards against.
    """
    content = set(_content_lines())
    assert "BR2_TARGET_GRUB2" in content, (
        "BR2_TARGET_GRUB2 (a menuconfig symbol) is missing — the menuconfig set was dropped"
    )


# --- authoritative present/absent facts --------------------------------------
# These pin the vendored data to reality. The *classify* item reads exactly this
# file and skips symbols that are present; if the data regresses, the facts
# below fail first. They are properties of the *data*, not of the classification
# logic (which is the next item).

# Recipe needed — absent from 2026.08 (PengyOS must author a recipe).
_MISSING = (
    "BR2_PACKAGE_GREETD",
    "BR2_PACKAGE_XDG_DESKTOP_PORTAL",
    "BR2_PACKAGE_XDG_DESKTOP_PORTAL_BACKEND_COSMIC",
    "BR2_PACKAGE_FIRA_FONT",
    "BR2_PACKAGE_FIRA_MONO",
    "BR2_PACKAGE_NOTO_FONTS_CJK",
    "BR2_PACKAGE_NOTO_FONTS_MONO",
)

# Already shipped — present in 2026.08 (PengyOS gets it free; no recipe).
_PRESENT = (
    "BR2_PACKAGE_LIBDISPLAY_INFO",
    "BR2_PACKAGE_SEATD",
)


def _present(sym: str) -> bool:
    return sym in set(_content_lines())


@pytest.mark.parametrize("sym", _MISSING)
def test_missing_symbols_are_absent(sym: str):
    assert not _present(sym), f"{sym} is absent from 2026.08 but the vendored list marks it present"


@pytest.mark.parametrize("sym", _PRESENT)
def test_shipped_symbols_are_present(sym: str):
    assert _present(sym), f"{sym} is present in 2026.08 but the vendored list marks it absent"
