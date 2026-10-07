"""Tests for the item-7 emitter: proposal building, JSON output, summary, explain.

Everything runs against the committed fixtures — no network, no root. These tests
lock the output contract: the ``run`` pipeline produces a deterministic, ranked,
justified set of proposals; ``proposals.json`` serializes and round-trips; and
``explain`` prints the full rationale chain with the deciding rule highlighted.
"""

import json
from pathlib import Path

import pytest

from pengyos_update_triage.advisories import load_advisories
from pengyos_update_triage.changelogs import load_changelogs
from pengyos_update_triage.classification import (
    CLASSIFY_IGNORE,
    CLASSIFY_RELEVANT,
    CLASSIFY_SECURITY,
    OUTCOME_FIRE,
)
from pengyos_update_triage.pins import load_pins, parse_pins
from pengyos_update_triage.proposal import (
    EXIT_FAIL,
    EXIT_OK,
    PROPOSALS_SCHEMA,
    build_proposal_set,
    explain_command,
    explain_proposal,
    load_proposals,
    proposals_to_dict,
    render_summary,
    run_command,
    write_proposals,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PINS = FIXTURES / "pins" / "pins_ok.toml"
ADVISORIES = FIXTURES / "advisories"
CHANGELOGS = FIXTURES / "changelogs"


def _inputs(pins_path=PINS):
    pins = load_pins(pins_path).pins
    advisories = load_advisories(ADVISORIES)
    changelogs = load_changelogs(CHANGELOGS)
    return pins, advisories, changelogs


# --- building the proposal set ---------------------------------------------


def test_build_proposal_set_fixture_counts():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    counts = pset.counts()
    assert counts["security"] == 4
    assert counts["relevant"] == 9
    assert counts["ignore"] == 32
    assert counts["proposals"] == 13
    assert pset.input_counts == {"pins": 6, "advisories": 6, "changelogs": 4}


def test_proposals_are_actionable_only_and_ranked():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    cats = {p.category for p in pset.proposals}
    assert cats <= {CLASSIFY_SECURITY, CLASSIFY_RELEVANT}
    # Ignored findings never leak into the proposal list.
    assert all(p.category != CLASSIFY_IGNORE for p in pset.proposals)
    assert all(p.category == CLASSIFY_IGNORE for p in pset.ignored)

    # Rank order: the top proposal is the critical curl advisory.
    top = pset.proposals[0]
    assert top.id == "advisory:CVE-2024-2392:curl"
    assert top.category == CLASSIFY_SECURITY
    assert top.severity == "critical"

    # The order field is a clean 1..N re-index over the emitted list.
    assert [p.order for p in pset.proposals] == list(range(1, len(pset.proposals) + 1))


def test_every_proposal_carries_justification():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    for p in pset.proposals + pset.ignored:
        assert p.rationale, "every proposal must carry a rationale"
        assert p.evidence, "every proposal must carry evidence"
        assert p.fired_rule, "every proposal must record the rule that fired"
        # The rationale chain must contain exactly one fired step.
        fired = [s for s in p.rationale_chain if s["outcome"] == OUTCOME_FIRE]
        assert len(fired) == 1
        assert fired[0]["rule"] == p.fired_rule
        # Source provenance is carried (the "evidence (source ids)" acceptance):
        # advisory proposals carry their id/aliases; changelog proposals carry a
        # source path + evidence even when they reference no CVE id.
        assert p.source
        assert p.evidence


def test_security_targets_and_identifiers():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    by_id = {p.id: p for p in pset.proposals}
    curl = by_id["advisory:CVE-2024-2392:curl"]
    assert curl.action == "update"
    assert curl.target_version == "8.9.0"
    assert curl.pin_version == "8.8.0"
    assert "CVE-2024-2392" in curl.identifiers
    # A curated advisory with a GHSA cross-reference carries both ids.
    glibc = by_id["advisory:CVE-2023-4911:glibc"]
    assert set(glibc.identifiers) == {"CVE-2023-4911", "GHSA-m77w-6vjw-wh2f"}
    # The last_affected advisory (zlib) has no fixed version → "patched build".
    zlib = by_id["advisory:CVE-2023-45853:zlib"]
    assert zlib.action == "update"
    assert zlib.target_version is None
    assert "a patched build" in zlib.version_range()


# --- min-severity filter ----------------------------------------------------


def test_min_severity_filters_quantified_security():
    pins, advisories, changelogs = _inputs()
    for min_sev, expected_security in (
        ("low", 4),
        ("medium", 4),
        ("high", 3),   # curl critical + glibc 7.8 + openssl 7.5 (both high)
        ("critical", 1),
    ):
        pset = build_proposal_set(pins, advisories, changelogs, min_sev)
        security = [p for p in pset.proposals if p.category == CLASSIFY_SECURITY]
        assert len(security) == expected_security, min_sev
        # No quantified severity below the minimum survives.
        from pengyos_update_triage.models import severity_rank

        for p in security:
            if p.severity != "none":
                assert severity_rank(p.severity) >= severity_rank(min_sev)
    # high → curl (critical), glibc (7.8) and openssl (7.5).
    high = build_proposal_set(pins, advisories, changelogs, "high")
    sec = sorted(p.id for p in high.proposals if p.category == CLASSIFY_SECURITY)
    assert sec == [
        "advisory:CVE-2023-4911:glibc",
        "advisory:CVE-2024-2392:curl",
        "advisory:CVE-2024-4741:openssl",
    ]


def test_min_severity_never_drops_unquantified():
    pins, advisories, changelogs = _inputs()
    # Relevant (changelog) findings are unquantified and must survive even the
    # strictest minimum.
    pset = build_proposal_set(pins, advisories, changelogs, "critical")
    relevant = [p for p in pset.proposals if p.category == CLASSIFY_RELEVANT]
    assert len(relevant) == 9
    assert all(p.severity == "none" for p in relevant)


# --- empty / degenerate inputs ---------------------------------------------


def test_empty_pins_yields_no_security_or_ignore():
    advisories = load_advisories(ADVISORIES)
    changelogs = load_changelogs(CHANGELOGS)
    pset = build_proposal_set(parse_pins({}), advisories, changelogs, "low")
    counts = pset.counts()
    assert counts["security"] == 0
    assert counts["ignore"] == 0
    # Unpinned changelog security signals are still surfaced as relevant.
    assert counts["relevant"] == 6
    assert all(p.category == CLASSIFY_RELEVANT for p in pset.proposals)


def test_zero_proposals_is_a_success(tmp_path):
    # A pins manifest + an advisory dir with no matches + no changelog signals
    # → zero proposals, but run_command is still a success (exit 0).
    pins_path = tmp_path / "pins.toml"
    pins_path.write_text(
        '[[component]]\nname = "only"\nversion = "1.0"\n', encoding="utf-8"
    )
    adv_dir = tmp_path / "adv"
    adv_dir.mkdir()
    chg_dir = tmp_path / "chg"
    chg_dir.mkdir()
    out = tmp_path / "proposals.json"
    rc = run_command(pins_path, adv_dir, chg_dir, out, "low")
    assert rc == EXIT_OK
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["counts"]["proposals"] == 0


# --- serialization ----------------------------------------------------------


def test_proposals_to_dict_shape_and_roundtrip():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    doc = proposals_to_dict(pset)
    assert doc["schema"] == PROPOSALS_SCHEMA
    assert "pengyos-update-triage" in doc["generator"]
    assert doc["min_severity"] == "low"
    assert set(doc) >= {"schema", "generator", "counts", "inputs", "proposals", "ignored"}
    # Must be JSON-serializable (the actual file payload).
    json.dumps(doc)

    # Round-trip: reload and check ids + rationale chain survive.
    reloaded_set = build_roundtrip(doc)
    assert [p.id for p in reloaded_set.proposals] == [p.id for p in pset.proposals]
    assert reloaded_set.proposals[0].rationale_chain == pset.proposals[0].rationale_chain
    assert reloaded_set.find(pset.proposals[0].id) is not None


def build_roundtrip(doc):
    from pengyos_update_triage.proposal import ProposalSet

    return ProposalSet.from_dict(doc)


def test_write_and_load_proposals_roundtrip(tmp_path):
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    out = tmp_path / "nested" / "proposals.json"
    write_proposals(pset, out)  # parent dir is created
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert text.endswith("\n")  # trailing newline
    loaded = load_proposals(out)
    assert [p.id for p in loaded.proposals] == [p.id for p in pset.proposals]
    top = loaded.proposals[0]
    assert top.category == CLASSIFY_SECURITY
    assert top.rationale_chain  # full chain present for explain


# --- summary ----------------------------------------------------------------


def test_render_summary_lists_each_proposal():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    text = render_summary(pset)
    assert "PengyOS update-triage" in text
    assert "13 proposal(s)" in text
    # Every proposal's component appears; the top id's component is listed.
    assert "curl" in text
    assert "openssl" in text
    # Version ranges show up (affected → target).
    assert "8.8.0 -> 8.9.0" in text
    # The ignored section is tallied.
    assert "Ignored 32 finding(s)" in text


def test_render_summary_empty():
    # Build an empty set directly.
    from pengyos_update_triage.proposal import ProposalSet

    pset = ProposalSet(proposals=[], ignored=[], min_severity="low",
                       input_counts={"pins": 0, "advisories": 0, "changelogs": 0})
    text = render_summary(pset)
    assert "0 proposal(s)" in text
    assert "nothing to update" in text


# --- explain ----------------------------------------------------------------


def test_explain_proposal_highlights_fired_rule():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    curl = pset.find("advisory:CVE-2024-2392:curl")
    text = explain_proposal(curl)
    assert text.startswith("Proposal: advisory:CVE-2024-2392:curl")
    assert "category:        security" in text
    assert "action:          update" in text
    # The fired rule is explicitly marked.
    assert "FIRED" in text
    assert "advisory-affected" in text
    # Evidence and identifiers are included.
    assert "Evidence:" in text
    assert "identifiers:" in text
    assert "CVE-2024-2392" in text


def test_explain_an_ignored_finding():
    pins, advisories, changelogs = _inputs()
    pset = build_proposal_set(pins, advisories, changelogs, "low")
    ign = pset.find("advisory:CVE-2023-38545:make")  # not-affected → ignore
    assert ign is not None
    assert ign.category == CLASSIFY_IGNORE
    text = explain_proposal(ign)
    assert "category:        ignore" in text
    assert "FIRED" in text


# --- command entry points ---------------------------------------------------


def test_run_command_writes_and_prints(capsys, tmp_path):
    out = tmp_path / "proposals.json"
    rc = run_command(PINS, ADVISORIES, CHANGELOGS, out, "low")
    assert rc == EXIT_OK
    captured = capsys.readouterr()
    assert "13 proposal(s)" in captured.out
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["counts"]["proposals"] == 13


def test_run_command_bad_pins_is_failure(capsys, tmp_path):
    rc = run_command(tmp_path / "nope.toml", ADVISORIES, CHANGELOGS, tmp_path / "o.json")
    assert rc == EXIT_FAIL
    err = capsys.readouterr().err
    assert "pengyos-update-triage" in err


def test_explain_command_success_and_errors(tmp_path):
    out = tmp_path / "proposals.json"
    assert run_command(PINS, ADVISORIES, CHANGELOGS, out, "low") == EXIT_OK

    rc = explain_command(out, "advisory:CVE-2024-2392:curl")
    assert rc == EXIT_OK

    # Unknown id → clean failure.
    assert explain_command(out, "no-such-id") == EXIT_FAIL
    # Missing file → clean failure.
    assert explain_command(tmp_path / "missing.json", "x") == EXIT_FAIL
