"""Tests for the repro report contract (repro_format.py)."""

from __future__ import annotations

import json

import pytest

from pengyos_verifier import repro_format as rf
from pengyos_verifier.checks import DEFAULT_REPRO_THRESHOLD


def make_report(score=1.0, identical=True, **extra):
    doc = {"score": score, "identical": identical}
    doc.update(extra)
    return doc


# --- validity -----------------------------------------------------------------

def test_valid_report_has_no_errors():
    assert rf.repro_report_errors(make_report()) == []
    assert rf.repro_report_errors(
        make_report(score=0.9, identical=False,
                    differing=[{"path": "a", "reason": "content"}],
                    missing=["b"])) == []


def test_top_level_must_be_an_object():
    for bad in ([1, 2], "score", 3, None, True):
        assert rf.repro_report_errors(bad) == ["report is not a JSON object"]


def test_required_fields():
    assert rf.repro_report_errors({"identical": True}) == \
        ["missing field 'score'"]
    assert rf.repro_report_errors({"score": 1.0}) == \
        ["missing field 'identical'"]


def test_score_must_be_a_number():
    for bad in (True, "1.0", None, [1.0], {}):
        assert "score must be a number" in "; ".join(
            rf.repro_report_errors(make_report(score=bad))), bad


def test_score_out_of_range():
    for bad in (-0.1, 1.5, 2.0):
        errors = rf.repro_report_errors(make_report(score=bad))
        assert any("outside [0, 1]" in e for e in errors), (bad, errors)


def test_score_boundaries_are_inclusive():
    assert rf.repro_report_errors(make_report(score=0.0)) == []
    assert rf.repro_report_errors(make_report(score=1)) == []  # int ok


def test_identical_must_be_a_boolean():
    for bad in ("yes", 1, None, [True]):
        assert "identical must be a boolean" in "; ".join(
            rf.repro_report_errors(make_report(identical=bad)))


def test_optional_fields_must_be_lists():
    for bad in ({"path": "a"}, "a", 3):
        assert "differing must be a list" in "; ".join(
            rf.repro_report_errors(make_report(differing=bad)))
    for bad in ("a", 3):
        assert "missing must be a list" in "; ".join(
            rf.repro_report_errors(make_report(missing=bad)))


# --- threshold verdict ---------------------------------------------------------

def test_threshold_boundary_passes():
    assert rf.score_error(make_report(score=1.0),
                          DEFAULT_REPRO_THRESHOLD) is None
    assert rf.score_error(make_report(score=0.999),
                          DEFAULT_REPRO_THRESHOLD) is not None
    assert rf.score_error(make_report(score=0.0), 0.0) is None
    msg = rf.score_error(make_report(score=0.5), 1.0)
    assert "0.5" in msg and "1.0" in msg


# --- parsing ---------------------------------------------------------------------

def test_parse_repro_report_happy_path():
    text = json.dumps(make_report(score=1.0))
    data, errors = rf.parse_repro_report(text)
    assert errors == []
    assert data == make_report(score=1.0)


def test_parse_repro_report_bad_json():
    data, errors = rf.parse_repro_report("{not json")
    assert data is None
    assert errors and errors[0].startswith("not valid JSON")


def test_parse_repro_report_invalid_document():
    data, errors = rf.parse_repro_report(json.dumps({"score": "1.0"}))
    assert data is None
    assert "missing field 'identical'" in errors
    assert any("score must be a number" in e for e in errors)
