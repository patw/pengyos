"""Tests for the VM-testable session-fact check engine.

Covers ``pengyos_desktop_session.check``:

* the session facts and their present patterns (derived from the emitted
  model, so the wayland-socket pattern tracks the operator-decided path);
* ``check_boot_log`` present/absent semantics — a fact passes when at least one
  of its present patterns is in the log;
* **no vacuous pass** — a fact with no present pattern in the log fails, and an
  empty log fails every fact (the engine fails closed);
* ``render_findings`` (PASS/FAIL markers, canonical order, verdict line) and
  ``outcome_to_dict`` (JSON structure);
* the committed boot-log fixtures (``tests/fixtures/``): the full log passes,
  a log missing the portal fails exactly the portal fact, an empty log fails
  all;
* the ``check --log`` CLI (EXIT_OK on pass, EXIT_FAIL on fail, findings /
  ``--json``); the ``check --ssh`` handler stays pending until item 5.
"""

import json
from pathlib import Path

import pytest

from pengyos_desktop_session import check
from pengyos_desktop_session import cli
from pengyos_desktop_session.check import (
    SESSION_FACTS,
    CheckOutcome,
    check_boot_log,
    present_patterns,
    render_findings,
    outcome_to_dict,
)
from pengyos_desktop_session.model import CosmicSession

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture()
def boot_pass():
    """The committed full COSMIC boot log (every fact present)."""
    return (FIXTURES / "boot_pass.log").read_text(encoding="utf-8")


@pytest.fixture()
def boot_missing_portal():
    """The committed log with the portal fact missing (one fact fails)."""
    return (FIXTURES / "boot_missing_portal.log").read_text(encoding="utf-8")


# --- the session facts -------------------------------------------------------


def test_session_facts_have_all_five():
    names = [f.name for f in SESSION_FACTS]
    assert names == [
        "cosmic-comp started",
        "a wayland socket exists",
        "cosmic-session / panel up",
        "pipewire / wireplumber running",
        "portals responding",
    ]


def test_facts_have_present_patterns():
    """Every fact is checked against at least one present pattern."""
    for fact in SESSION_FACTS:
        assert fact.present, f"{fact.name} has no present patterns"
        assert present_patterns(fact) == fact.present


def test_wayland_socket_pattern_tracks_model():
    """The wayland-socket fact's pattern is the resolved model socket path."""
    socket_fact = next(f for f in SESSION_FACTS if f.name == "a wayland socket exists")
    assert CosmicSession().wayland_socket(1000) in socket_fact.present


def test_facts_are_deterministically_ordered():
    """Facts are reported in a stable, canonical order."""
    names = [f.name for f in SESSION_FACTS]
    assert names == sorted(names, key=lambda n: check.FINDINGS_ORDER.index(n))


# --- matching: present/absent semantics --------------------------------------


def test_fact_matches_when_a_present_pattern_is_in_log(boot_pass):
    comp = next(f for f in SESSION_FACTS if f.name == "cosmic-comp started")
    assert comp.matches(boot_pass) is True


def test_fact_fails_when_no_present_pattern_in_log(boot_pass):
    """A present pattern missing from the log fails the fact (no vacuous pass)."""
    comp = next(f for f in SESSION_FACTS if f.name == "cosmic-comp started")
    assert comp.matches("the log never mentions the compositor") is False


def test_pipewire_fact_matches_either_pattern():
    """The pipewire/wireplumber fact passes when EITHER daemon is present."""
    pw = next(f for f in SESSION_FACTS if f.name == "pipewire / wireplumber running")
    assert pw.matches("pipewire[1]: started") is True
    assert pw.matches("wireplumber[2]: ready") is True
    assert pw.matches("nothing of interest") is False


# --- check_boot_log: the no-vacuous-pass engine ------------------------------


def test_check_passes_on_full_log(boot_pass):
    outcome = check_boot_log(boot_pass)
    assert outcome.ok is True
    assert len(outcome.passed) == len(SESSION_FACTS)
    assert len(outcome.failed) == 0


def test_check_fails_when_a_present_pattern_is_missing(boot_missing_portal):
    """A log that omits one fact's pattern fails that fact (and the check)."""
    outcome = check_boot_log(boot_missing_portal)
    assert outcome.ok is False
    failed = {f.name for f in outcome.failed}
    assert failed == {"portals responding"}
    # the other four facts are still present and pass
    assert len(outcome.passed) == len(SESSION_FACTS) - 1


def test_empty_log_fails_every_fact_no_vacuous_pass():
    """An empty (or irrelevant) log fails every fact — no vacuous pass."""
    outcome = check_boot_log("")
    assert outcome.ok is False
    assert {f.name for f in outcome.failed} == {f.name for f in SESSION_FACTS}
    assert outcome.passed == ()


def test_irrelevant_log_fails_every_fact():
    """A log that mentions none of the patterns fails every fact."""
    outcome = check_boot_log("the quick brown fox jumps over the lazy dog\n")
    assert outcome.ok is False
    assert len(outcome.failed) == len(SESSION_FACTS)


def test_check_records_source(boot_pass):
    outcome = check_boot_log(boot_pass, source="boot log")
    assert outcome.source == "boot log"


def test_check_is_deterministic(boot_pass):
    a = check_boot_log(boot_pass)
    b = check_boot_log(boot_pass)
    assert outcome_to_dict(a) == outcome_to_dict(b)


def test_custom_facts_override():
    """A caller may check a different (even empty) fact set."""
    outcome = check_boot_log(
        "cosmic-comp came up",
        facts=(check.Fact("only this", ("cosmic-comp",), "x"),),
    )
    assert len(outcome.findings) == 1
    assert outcome.ok is True


# --- render_findings ---------------------------------------------------------


def test_render_findings_passes_report(boot_pass):
    text = render_findings(check_boot_log(boot_pass))
    assert "RESULT: PASS" in text
    for name in check.FINDINGS_ORDER:
        assert name in text
    assert "[PASS]" in text
    assert "[FAIL]" not in text


def test_render_findings_fails_report_lists_missing(boot_missing_portal):
    text = render_findings(check_boot_log(boot_missing_portal))
    assert "RESULT: FAIL" in text
    assert "portals responding" in text


def test_render_findings_lists_in_canonical_order(boot_pass):
    text = render_findings(check_boot_log(boot_pass))
    positions = [text.index(name) for name in check.FINDINGS_ORDER]
    assert positions == sorted(positions)


def test_render_findings_shows_patterns(boot_pass):
    text = render_findings(check_boot_log(boot_pass))
    # each fact line names at least one of its present patterns
    assert "cosmic-comp" in text


# --- outcome_to_dict (JSON) --------------------------------------------------


def test_outcome_to_dict_structure(boot_pass):
    payload = outcome_to_dict(check_boot_log(boot_pass))
    assert payload["ok"] is True
    assert payload["source"] == "boot log"
    assert [f["name"] for f in payload["findings"]] == list(check.FINDINGS_ORDER)
    assert all(f["ok"] is True for f in payload["findings"])
    assert all(f["patterns"] for f in payload["findings"])


def test_outcome_to_dict_is_json_roundtrip(boot_missing_portal):
    payload = outcome_to_dict(check_boot_log(boot_missing_portal))
    # JSON-serialisable and lossless through a round-trip
    roundtrip = json.loads(json.dumps(payload))
    assert roundtrip["ok"] is False
    assert [f["name"] for f in roundtrip["findings"] if not f["ok"]] == ["portals responding"]


# --- check CLI ---------------------------------------------------------------


def test_check_cli_log_pass_returns_ok(tmp_path, boot_pass, capsys):
    log = tmp_path / "boot.log"
    log.write_text(boot_pass, encoding="utf-8")
    rc = cli.main(["check", "--log", str(log)])
    assert rc == cli.EXIT_OK
    assert "RESULT: PASS" in capsys.readouterr().out


def test_check_cli_log_fail_returns_fail(tmp_path, boot_missing_portal, capsys):
    log = tmp_path / "boot.log"
    log.write_text(boot_missing_portal, encoding="utf-8")
    rc = cli.main(["check", "--log", str(log)])
    assert rc == cli.EXIT_FAIL
    out = capsys.readouterr().out
    assert "RESULT: FAIL" in out
    assert "portals" in out


def test_check_cli_log_empty_fails(tmp_path, capsys):
    log = tmp_path / "empty.log"
    log.write_text("", encoding="utf-8")
    rc = cli.main(["check", "--log", str(log)])
    assert rc == cli.EXIT_FAIL


def test_check_cli_json_on_pass(tmp_path, boot_pass):
    log = tmp_path / "boot.log"
    log.write_text(boot_pass, encoding="utf-8")
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_desktop_session", "check", "--log", str(log), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert len(payload["findings"]) == len(SESSION_FACTS)


def test_check_cli_json_on_fail(tmp_path, boot_missing_portal):
    log = tmp_path / "boot.log"
    log.write_text(boot_missing_portal, encoding="utf-8")
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "pengyos_desktop_session", "check", "--log", str(log), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == cli.EXIT_FAIL, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False


# The `check --ssh` opt-in probe is covered in test_ssh.py (item 5).
