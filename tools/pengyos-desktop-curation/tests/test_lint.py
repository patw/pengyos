"""Tests for the human-artistry rule.

Covers the lint deliverable:

* the committed ``curated.toml`` passes the artistry rule (every entry has a
  rationale and is ``human_signed``),
* :func:`lint` reports a violation per entry that is missing a rationale or is
  unsigned, in source order,
* :class:`LintReport` / :class:`LintViolation` render as deterministic JSON and
  human-readable text,
* :func:`assert_clean` is the strict gate (raises :class:`ArtistryError` when
  dirty),
* the CLI ``lint`` exits 0 on a clean set and 1 on a dirty one (the committed
  set is clean; a dirty one is driven via a mocked loader).

No network, root, display, or hardware is needed.
"""

from __future__ import annotations

import json

import pytest

from pengyos_desktop_curation import cli
from pengyos_desktop_curation import lint
from pengyos_desktop_curation.data import (
    CuratedEntry,
    CuratedSet,
    parse_entries,
)

# A clean two-entry set.
CLEAN = parse_entries(
    [
        {
            "category": "browser",
            "app": "Firefox",
            "lane": "flatpak",
            "ref": "r1",
            "rationale": "default browser",
            "human_signed": True,
        },
        {
            "category": "media",
            "app": "VLC",
            "lane": "flatpak",
            "ref": "r2",
            "rationale": "plays everything",
            "human_signed": True,
        },
    ]
)

# A dirty set: index 0 missing rationale, index 1 unsigned, index 2 clean.
DIRTY = CuratedSet(
    (
        CuratedEntry(category="browser", app="F", lane="appimage", rationale=""),
        CuratedEntry(category="media", app="V", lane="appimage", rationale="ok"),
        CuratedEntry(
            category="office",
            app="LO",
            lane="appimage",
            rationale="ok",
            human_signed=False,
        ),
    )
)


# --- lint_default: the committed data ----------------------------------------


def test_lint_default_is_clean() -> None:
    """The committed curated set passes the artistry rule — the headline
    acceptance test for item 8."""

    assert lint.lint_default().is_clean


def test_committed_every_entry_has_rationale_and_is_signed() -> None:
    """Directly restates the artistry rule against the committed data: no entry
    is unsigned or rationale-less."""

    report = lint.lint_default()
    assert report.is_clean
    assert report.violations == ()


# --- lint: the core contract -------------------------------------------------


def test_lint_clean_set_has_no_violations() -> None:
    report = lint.lint(CLEAN)
    assert report.is_clean is True
    assert report.violations == ()


def test_lint_empty_set_is_clean() -> None:
    """An empty set violates nothing — nothing raises, and it is clean."""

    assert lint.lint(CuratedSet()).is_clean is True


def test_lint_reports_missing_rationale() -> None:
    report = lint.lint(DIRTY)
    assert report.is_clean is False
    # index 0 is missing a rationale.
    assert report.violations[0] == lint.LintViolation(0, "F", "missing rationale")


def test_lint_reports_unsigned_entry() -> None:
    report = lint.lint(DIRTY)
    # index 1 (unsigned) and index 2 (unsigned) both violate; index 0 too.
    reasons = {(v.index, v.reason) for v in report.violations}
    assert (0, "missing rationale") in reasons
    assert (1, "not human_signed") in reasons
    assert (2, "not human_signed") in reasons


def test_lint_reports_in_source_order() -> None:
    report = lint.lint(DIRTY)
    assert [v.index for v in report.violations] == [0, 1, 2]


def test_lint_never_raises_on_dirty_set() -> None:
    """A dirty set is reported, not crashed."""

    report = lint.lint(DIRTY)
    assert isinstance(report, lint.LintReport)


# --- LintViolation -----------------------------------------------------------


def test_lint_violation_as_dict() -> None:
    violation = lint.lint(DIRTY).violations[0]
    assert violation.as_dict() == {
        "index": violation.index,
        "app": violation.app,
        "reason": violation.reason,
    }


# --- LintReport --------------------------------------------------------------


def test_report_is_clean_predicate() -> None:
    assert lint.lint(CLEAN).is_clean is True
    assert lint.lint(DIRTY).is_clean is False


def test_report_as_dict() -> None:
    payload = lint.lint(DIRTY).as_dict()
    assert payload["clean"] is False
    assert isinstance(payload["violations"], list)
    assert len(payload["violations"]) == 3


def test_report_as_dict_clean() -> None:
    payload = lint.lint(CLEAN).as_dict()
    assert payload["clean"] is True
    assert payload["violations"] == []


def test_report_to_json_is_valid_and_deterministic() -> None:
    a = lint.lint(DIRTY).to_json()
    b = lint.lint(DIRTY).to_json()
    assert a == b
    payload = json.loads(a)  # parses as JSON
    assert payload["clean"] is False
    assert len(payload["violations"]) == 3


def test_report_to_json_clean_has_empty_violations() -> None:
    payload = json.loads(lint.lint(CLEAN).to_json())
    assert payload["clean"] is True
    assert payload["violations"] == []


def test_report_to_text_clean() -> None:
    text = lint.lint(CLEAN).to_text()
    assert "OK" in text
    assert "FAILED" not in text


def test_report_to_text_dirty_lists_violations() -> None:
    text = lint.lint(DIRTY).to_text()
    assert "FAILED" in text
    assert "missing rationale" in text
    assert "not human_signed" in text
    # each violation is named with its index and app.
    assert "#0 F" in text
    assert "#1 V" in text
    assert "#2 LO" in text


# --- assert_clean: the strict gate -------------------------------------------


def test_assert_clean_passes_for_clean_set() -> None:
    assert lint.assert_clean(CLEAN) is CLEAN


def test_assert_clean_raises_for_dirty_set() -> None:
    with pytest.raises(lint.ArtistryError) as exc:
        lint.assert_clean(DIRTY)
    message = str(exc.value)
    assert "missing rationale" in message
    assert "not human_signed" in message


def test_artistry_error_is_curated_error() -> None:
    assert issubclass(lint.ArtistryError, lint.CuratedError)


# --- CLI: lint ---------------------------------------------------------------


def test_cli_lint_clean_exits_zero(capsys) -> None:
    rc = cli.main(["lint"])
    assert rc == cli.EXIT_OK
    assert "OK" in capsys.readouterr().out


def test_cli_lint_clean_json_exits_zero(capsys) -> None:
    rc = cli.main(["lint", "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["clean"] is True


def test_cli_lint_dirty_exits_one(monkeypatch, capsys) -> None:
    """A dirty set fails (exit 1) and reports the violations, not a crash.

    The committed data is clean (exit 0); here we mock the loader to the dirty
    fixture so the CLI exercises the failure path.
    """

    monkeypatch.setattr("pengyos_desktop_curation.cli.load_default", lambda: DIRTY)
    rc = cli.main(["lint"])
    assert rc == cli.EXIT_FAIL
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "missing rationale" in out


def test_cli_lint_dirty_json_exits_one(monkeypatch, capsys) -> None:
    monkeypatch.setattr("pengyos_desktop_curation.cli.load_default", lambda: DIRTY)
    rc = cli.main(["lint", "--json"])
    assert rc == cli.EXIT_FAIL
    payload = json.loads(capsys.readouterr().out)
    assert payload["clean"] is False
    assert len(payload["violations"]) == 3


# --- CLI argument surface ---------------------------------------------------


def test_lint_parser_takes_json_flag() -> None:
    args = cli.build_parser().parse_args(["lint", "--json"])
    assert args.command == "lint"
    assert args.json is True


def test_lint_json_defaults_false() -> None:
    args = cli.build_parser().parse_args(["lint"])
    assert args.command == "lint"
    assert args.json is False


def test_lint_in_documented_subcommands() -> None:
    assert "lint" in cli.SUBCOMMANDS
    assert cli.SUBCOMMANDS == ("list", "coverage", "emit", "lint")
