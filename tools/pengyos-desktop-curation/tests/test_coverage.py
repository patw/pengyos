"""Tests for the coverage report.

Covers the coverage deliverable:

* a missing everyday category is a **reported gap**, not a crash,
* a complete set passes,
* the report renders as deterministic JSON and human-readable text,
* the report is ordered by the everyday-list order,
* the CLI ``coverage`` exits 0 on a complete set and 1 on a gap set.

No network, root, display, or hardware is needed.
"""

from __future__ import annotations

import json

from pengyos_desktop_curation import coverage
from pengyos_desktop_curation import cli
from pengyos_desktop_curation.data import (
    EVERYDAY_CATEGORIES,
    CuratedSet,
    load_default,
    parse_entries,
)

# Two-entry set: covers 'browser' and 'terminal' only.
TWO = parse_entries(
    [
        {"category": "browser", "app": "Firefox", "lane": "flatpak", "ref": "r1", "rationale": "x"},
        {"category": "terminal", "app": "Terminal", "lane": "flatpak", "ref": "r2", "rationale": "y"},
    ]
)

# The committed set: one entry per everyday category.
SET = load_default()


def _entry(category: str, app: str = "App") -> dict:
    return {
        "category": category,
        "app": app,
        "lane": "flatpak",
        "ref": f"ref.{app}",
        "rationale": "ratified",
    }


# --- build_report: the core contract -----------------------------------------


def test_build_report_missing_category_is_gap_not_crash() -> None:
    """The headline contract: a missing everyday category is a reported gap,
    not a crash."""

    report = coverage.build_report(TWO)
    assert report.is_complete is False
    # 'browser' and 'terminal' are covered; the other eight are gaps.
    assert report.gaps == tuple(EVERYDAY_CATEGORIES[2:])
    assert report.covered_count == 2
    assert report.total_count == len(EVERYDAY_CATEGORIES)


def test_build_report_empty_set_is_all_gaps() -> None:
    """An empty curated set covers nothing — every everyday category is a gap,
    but nothing raises."""

    report = coverage.build_report(CuratedSet())
    assert report.is_complete is False
    assert report.gaps == EVERYDAY_CATEGORIES
    assert report.covered_count == 0


def test_build_report_complete_set_passes() -> None:
    report = coverage.build_report(SET)
    assert report.is_complete is True
    assert report.gaps == ()
    assert report.covered_count == report.total_count == 10


def test_build_report_orders_by_everyday_list() -> None:
    """The per-category status follows the everyday-list order, not the
    source-data order."""

    report = coverage.build_report(TWO)
    assert tuple(s.category for s in report.status) == EVERYDAY_CATEGORIES


def test_build_report_custom_everyday_list() -> None:
    """build_report accepts a caller-supplied everyday list."""

    report = coverage.build_report(TWO, everyday=("browser", "media"))
    assert tuple(s.category for s in report.status) == ("browser", "media")
    assert report.gaps == ("media",)


def test_build_report_multiple_entries_in_a_category() -> None:
    """A category with several entries is still covered once; apps accumulate."""

    multi = parse_entries(
        [
            _entry("browser", "Firefox"),
            _entry("browser", "Chromium"),
        ]
    )
    report = coverage.build_report(multi, everyday=("browser",))
    status = report.status[0]
    assert status.covered is True
    assert status.apps == ("Firefox", "Chromium")


# --- CategoryStatus ----------------------------------------------------------


def test_category_status_covered_and_missing() -> None:
    report = coverage.build_report(TWO)
    covered = next(s for s in report.status if s.category == "browser")
    missing = next(s for s in report.status if s.category == "media")
    assert covered.covered is True
    assert covered.apps == ("Firefox",)
    assert missing.covered is False
    assert missing.apps == ()


def test_category_status_as_dict() -> None:
    report = coverage.build_report(TWO)
    status = next(s for s in report.status if s.category == "browser")
    assert status.as_dict() == {
        "category": "browser",
        "covered": True,
        "apps": ["Firefox"],
    }


def test_category_status_to_text_covered_and_missing() -> None:
    report = coverage.build_report(TWO)
    covered = next(s for s in report.status if s.category == "browser")
    missing = next(s for s in report.status if s.category == "media")
    assert covered.to_text() == "  ✓ browser     Firefox"
    assert missing.to_text() == "  ✗ media       MISSING"


# --- CoverageReport rendering ------------------------------------------------


def test_report_to_json_is_valid_and_deterministic() -> None:
    a = coverage.build_report(TWO).to_json()
    b = coverage.build_report(TWO).to_json()
    assert a == b
    payload = json.loads(a)  # parses as JSON
    assert payload["complete"] is False
    assert payload["everyday_count"] == 10
    assert payload["covered_count"] == 2
    assert payload["gaps"] == list(EVERYDAY_CATEGORIES[2:])


def test_report_to_json_complete_has_empty_gaps() -> None:
    payload = json.loads(coverage.build_report(SET).to_json())
    assert payload["complete"] is True
    assert payload["gaps"] == []
    assert payload["covered_count"] == 10


def test_report_to_text_has_header_and_verdict() -> None:
    text = coverage.build_report(TWO).to_text()
    assert "US-DSK-2 everyday list" in text
    assert "8 gap(s)" in text
    assert "media" in text  # a gap name appears
    assert "2/10" in text  # the covered/total ratio


def test_report_to_text_complete_verdict() -> None:
    text = coverage.build_report(SET).to_text()
    assert "complete" in text
    assert "10/10" in text
    assert "MISSING" not in text


def test_report_to_text_lists_every_everyday_category() -> None:
    text = coverage.build_report(SET).to_text()
    for category in EVERYDAY_CATEGORIES:
        assert category in text


# --- CLI: coverage -----------------------------------------------------------


def test_cli_coverage_complete_exits_zero(capsys) -> None:
    rc = cli.main(["coverage"])
    assert rc == cli.EXIT_OK
    assert "complete" in capsys.readouterr().out


def test_cli_coverage_json_exits_zero(capsys) -> None:
    rc = cli.main(["coverage", "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True


def test_cli_coverage_reports_gap_with_exit_one(monkeypatch, capsys) -> None:
    """A coverage gap is a reported failure (exit 1), not a crash.

    The committed data is complete (exit 0); here we mock the loader to a
    two-entry set so the report has gaps and assert the CLI's exit-1 contract.
    """

    monkeypatch.setattr("pengyos_desktop_curation.cli.load_default", lambda: TWO)
    rc = cli.main(["coverage"])
    assert rc == cli.EXIT_FAIL
    out = capsys.readouterr().out
    assert "8 gap(s)" in out  # the gap is reported, not swallowed


def test_cli_coverage_complete_exits_zero_text_and_json() -> None:
    """The committed set is complete, so both the text and JSON CLI paths exit 0."""

    assert cli.main(["coverage"]) == cli.EXIT_OK
    assert cli.main(["coverage", "--json"]) == cli.EXIT_OK
