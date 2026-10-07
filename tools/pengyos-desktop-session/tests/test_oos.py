"""Tests for the explicitly-out-of-scope facts.

Covers :mod:`pengyos_desktop_session.oos` and the way the check engine reports
the out-of-scope facts (never silently omitted):

* :data:`OUT_OF_SCOPE` encodes the four real-hardware categories — GPU, audio,
  Bluetooth, suspend/resume — each with a *what* and a *why*;
* :func:`render_out_of_scope` renders each as a "real-hardware, not verified
  here" bullet;
* the check engine threads the out-of-scope facts onto :class:`CheckOutcome`
  (default = :data:`OUT_OF_SCOPE`, overridable);
* :func:`render_findings` appends the out-of-scope section on **every** verdict
  — PASS, FAIL, and SKIP — so they are never dropped;
* :func:`outcome_to_dict` includes them in the JSON surface.
"""

import json
from pathlib import Path

import pytest

from pengyos_desktop_session import check
from pengyos_desktop_session import oos
from pengyos_desktop_session.check import CheckOutcome, check_boot_log
from pengyos_desktop_session.model import CosmicSession

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture()
def boot_pass():
    return (FIXTURES / "boot_pass.log").read_text(encoding="utf-8")


# --- oos module: the documented list ----------------------------------------


def test_out_of_scope_has_four_real_hardware_categories():
    """The four explicitly-out-of-scope real-hardware facts are encoded."""
    categories = [f.category for f in oos.OUT_OF_SCOPE]
    assert categories == ["GPU", "audio", "Bluetooth", "suspend/resume"]


def test_out_of_scope_facts_have_what_and_why():
    """Every out-of-scope fact names what it covers and why it is out of scope."""
    for fact in oos.OUT_OF_SCOPE:
        assert fact.category
        assert fact.what, f"{fact.category} has no 'what'"
        assert fact.why, f"{fact.category} has no 'why'"
        # the note honestly explains the real-hardware / not-in-VM reason
        lowered = fact.why.lower()
        assert any(token in lowered for token in ("hardware", "qemu", "vm", "radio"))


def test_out_of_scope_is_frozen_and_ordered():
    """The list is a frozen, canonically ordered tuple."""
    assert isinstance(oos.OUT_OF_SCOPE, tuple)
    assert [f.category for f in oos.OUT_OF_SCOPE] == ["GPU", "audio", "Bluetooth", "suspend/resume"]


def test_render_out_of_scope_lists_every_category():
    text = oos.render_out_of_scope()
    for category in ("GPU", "audio", "Bluetooth", "suspend/resume"):
        assert category in text


def test_render_out_of_scope_has_real_hardware_label():
    """The section is labelled "real-hardware, not verified here"."""
    text = oos.render_out_of_scope()
    assert oos.OUT_OF_SCOPE_LABEL in text
    assert "real-hardware" in text
    assert "not verified here" in text


def test_render_out_of_scope_why_is_shown():
    text = oos.render_out_of_scope()
    # each fact's real-hardware "why" is surfaced
    assert "no real GPU" in text
    assert "no real audio hardware" in text


def test_render_out_of_scope_respects_override():
    """A caller may pass a custom fact set (e.g. an empty one)."""
    text = oos.render_out_of_scope(())
    assert text.startswith(oos.OUT_OF_SCOPE_LABEL)
    # no bullets when the list is empty
    assert "GPU" not in text


# --- check engine: out-of-scope facts are threaded onto the outcome ---------


def test_check_boot_log_carries_out_of_scope_by_default(boot_pass):
    """By default the outcome carries the canonical out-of-scope facts."""
    outcome = check_boot_log(boot_pass)
    assert [f.category for f in outcome.out_of_scope] == [
        "GPU",
        "audio",
        "Bluetooth",
        "suspend/resume",
    ]


def test_check_boot_log_out_of_scope_is_overridable(boot_pass):
    """A caller may pass a custom out-of-scope list."""
    custom = (oos.OutOfScopeFact("net", "network", "no NIC in the VM"),)
    outcome = check_boot_log(boot_pass, out_of_scope=custom)
    assert [f.category for f in outcome.out_of_scope] == ["net"]


def test_check_boot_log_empty_out_of_scope_allowed(boot_pass):
    """The out-of-scope list can be explicitly empty."""
    outcome = check_boot_log(boot_pass, out_of_scope=())
    assert outcome.out_of_scope == ()


def test_check_out_of_scope_default_matches_module():
    """The default out-of-scope facts equal the module's canonical list."""
    outcome = check_boot_log("")  # any log
    assert outcome.out_of_scope == oos.OUT_OF_SCOPE


# --- render_findings: the out-of-scope section on every verdict -------------


def test_render_findings_pass_shows_out_of_scope(boot_pass):
    text = check.render_findings(check_boot_log(boot_pass))
    assert oos.OUT_OF_SCOPE_LABEL in text
    assert "GPU" in text
    # a passing report still lists the findings
    assert "RESULT: PASS" in text


def test_render_findings_fail_shows_out_of_scope():
    """Even a FAIL report lists the out-of-scope facts (never omitted)."""
    text = check.render_findings(check_boot_log("irrelevant log"))
    assert oos.OUT_OF_SCOPE_LABEL in text
    assert "RESULT: FAIL" in text


def test_render_findings_skip_shows_out_of_scope():
    """A SKIP report also lists the out-of-scope facts (never omitted)."""
    outcome = CheckOutcome(
        ok=False, findings=(), source="ssh:host", skip="PENGYOS_IT not set"
    )
    text = check.render_findings(outcome)
    assert "RESULT: SKIP" in text
    assert oos.OUT_OF_SCOPE_LABEL in text
    # a skip lists no PASS/FAIL fact lines
    assert "[PASS]" not in text
    assert "[FAIL]" not in text


def test_render_findings_out_of_scope_tracks_outcome(boot_pass):
    """The rendered section reflects the outcome's out-of-scope list."""
    custom = (oos.OutOfScopeFact("net", "network", "no NIC"),)
    text = check.render_findings(check_boot_log(boot_pass, out_of_scope=custom))
    assert "network" in text
    # the default GPU bullet is absent because the list was overridden
    assert "no real GPU" not in text


# --- outcome_to_dict: the out-of-scope facts in JSON ------------------------


def test_outcome_to_dict_has_out_of_scope(boot_pass):
    payload = check.outcome_to_dict(check_boot_log(boot_pass))
    assert isinstance(payload["out_of_scope"], list)
    assert len(payload["out_of_scope"]) == 4
    first = payload["out_of_scope"][0]
    assert set(first) == {"category", "what", "why"}
    assert first["category"] == "GPU"
    assert first["what"] and first["why"]


def test_outcome_to_dict_out_of_scope_is_json_roundtrip(boot_pass):
    payload = check.outcome_to_dict(check_boot_log(boot_pass))
    roundtrip = json.loads(json.dumps(payload))
    categories = [f["category"] for f in roundtrip["out_of_scope"]]
    assert categories == ["GPU", "audio", "Bluetooth", "suspend/resume"]
    assert all(f["what"] and f["why"] for f in roundtrip["out_of_scope"])


def test_outcome_to_dict_respects_override(boot_pass):
    custom = (oos.OutOfScopeFact("net", "network", "no NIC"),)
    payload = check.outcome_to_dict(check_boot_log(boot_pass, out_of_scope=custom))
    assert [f["category"] for f in payload["out_of_scope"]] == ["net"]


# --- the four categories are the real-hardware ones the spec names ----------


def test_out_of_scope_covers_spec_four():
    """The spec's four real-hardware facts (GPU/audio/BT/suspend) are all present."""
    text = oos.render_out_of_scope()
    # GPU acceleration, audio devices, Bluetooth, suspend/resume
    assert "GPU" in text
    assert "audio" in text
    assert "Bluetooth" in text
    assert "suspend/resume" in text
