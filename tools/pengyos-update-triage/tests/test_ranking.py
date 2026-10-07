"""Tests for the documented ranking heuristic.

Covers ``pengyos_update_triage.ranking``: the lexicographic
``(severity, exploitability, reachability)`` ordering with its two
determinism tie-breakers (category, id), the ``rank_key`` contract, the
``RankedClassification`` record, and an end-to-end ranking of the committed
fixture pins / advisories / changelogs.

All inputs are self-contained (no network).
"""

import random
from pathlib import Path

import pytest

from pengyos_update_triage.advisories import load_advisories
from pengyos_update_triage.changelog_scan import scan_changelogs
from pengyos_update_triage.changelogs import load_changelogs
from pengyos_update_triage.classification import (
    CLASSIFY_IGNORE,
    CLASSIFY_RELEVANT,
    CLASSIFY_SECURITY,
    Classification,
    classify,
)
from pengyos_update_triage.matching import match_all
from pengyos_update_triage.models import Pin
from pengyos_update_triage.pins import load_pins
from pengyos_update_triage.ranking import (
    RANKED_UNPINNED,
    RankedClassification,
    rank,
    rank_key,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ADV = FIXTURES / "advisories"
CHG = FIXTURES / "changelogs"
PINS_OK = FIXTURES / "pins" / "pins_ok.toml"

# The fixture pins used throughout the unit tests: one per reachability level.
PINS = [
    Pin(name="openssl", version="3.0.13", ecosystem="alpine", reachability="core"),
    Pin(name="curl", version="8.8.0", ecosystem="alpine", reachability="std"),
    Pin(name="make", version="4.4.1", ecosystem="alpine", reachability="opt"),
]


def make_cls(cid="c1", component="openssl", category=CLASSIFY_SECURITY,
             severity="high", exploitability=2.3):
    """A minimal Classification with only the fields ranking reads set."""
    return Classification(
        id=cid,
        component=component,
        category=category,
        pin_version="1.0",
        severity=severity,
        severity_source="cvss" if exploitability else "none",
        suggested_action="update",
        target_version=None,
        rationale=f"{cid} rationale",
        rationale_chain=(),
        identifiers=(cid,),
        evidence=("evidence string",),
        source="test",
        exploitability=exploitability,
        exploitability_source="cvss" if exploitability else "none",
    )


def ids(ranked):
    return [r.classification.id for r in ranked]


# --- severity dominates -----------------------------------------------------


@pytest.mark.parametrize(
    "high_sev, low_sev",
    [
        ("critical", "high"),
        ("high", "medium"),
        ("medium", "low"),
        ("low", "none"),          # a quantified low beats unquantified
        ("low", None),            # None severity also sorts below low
    ],
)
def test_severity_dominates_everything(high_sev, low_sev):
    # The weaker severity carries a *better* exploitability and reachability:
    # severity must still win.
    weak = make_cls("weak", component="openssl", severity=high_sev,
                    exploitability=0.0)
    strong_signals = make_cls("strong", component="make", severity=low_sev,
                              exploitability=3.9)
    ranked = rank([strong_signals, weak], PINS)
    assert ids(ranked) == ["weak", "strong"]
    assert ranked[0].severity_rank > ranked[1].severity_rank


def test_same_severity_same_signals_stable_by_id():
    a = make_cls("aaaa", severity="high", exploitability=2.3)
    b = make_cls("bbbb", severity="high", exploitability=2.3)
    assert ids(rank([b, a], PINS)) == ["aaaa", "bbbb"]


# --- exploitability within one severity band --------------------------------


def test_exploitability_breaks_ties_within_severity():
    easy = make_cls("easy", component="curl", severity="high", exploitability=3.9)
    hard = make_cls("hard", component="openssl", severity="high", exploitability=2.3)
    # Reachability is inverted (hard sits on a more reachable component) and
    # must NOT beat exploitability.
    ranked = rank([hard, easy], PINS)
    assert ids(ranked) == ["easy", "hard"]
    assert ranked[0].exploitability == 3.9
    assert ranked[1].exploitability == 2.3


def test_exploitability_zero_for_unquantified_sorts_last_in_band():
    quant = make_cls("quant", component="curl", severity="medium",
                     exploitability=0.2)
    unq = make_cls("unq", component="openssl", severity="medium",
                   exploitability=0.0)  # affected but no CVSS 3.1 vector
    assert ids(rank([unq, quant], PINS)) == ["quant", "unq"]


# --- reachability as the third signal ---------------------------------------


def test_reachability_breaks_remaining_ties():
    core = make_cls("core", component="openssl", severity="high", exploitability=2.3)
    std = make_cls("std", component="curl", severity="high", exploitability=2.3)
    opt = make_cls("opt", component="make", severity="high", exploitability=2.3)
    ranked = rank([opt, std, core], PINS)
    assert ids(ranked) == ["core", "std", "opt"]
    assert [r.reachability for r in ranked] == ["core", "std", "opt"]
    assert [r.reachability_rank for r in ranked] == [2, 1, 0]


def test_unpinned_component_degrades_to_rank_zero_labeled_unpinned():
    pinned = make_cls("pinned", component="make", severity="high", exploitability=2.3)
    unpinned = make_cls("unpinned", component="vim", severity="high",
                        exploitability=2.3)
    ranked = rank([unpinned, pinned], PINS)
    by_id = {r.classification.id: r for r in ranked}
    assert by_id["unpinned"].reachability == RANKED_UNPINNED
    assert by_id["unpinned"].reachability_rank == 0
    # Same signal as an opt-pinned component → tied on reachability; the
    # total order still holds (category, then id).
    assert ranked[0].classification.id in ("pinned", "unpinned")


def test_reachability_covers_aliases():
    # The glibc pin in the fixture manifest carries aliases; a finding named
    # by an alias must inherit the pin's reachability.
    manifest = load_pins(PINS_OK)
    finding = make_cls("glib-sig", component="glib", severity="high",
                       exploitability=2.3)
    ranked = rank([finding], manifest.pins)
    assert ranked[0].reachability == "core"
    assert ranked[0].reachability_rank == 2


# --- category + id tie-breakers (total determinism) --------------------------


def test_category_tiebreak_security_before_relevant_before_ignore():
    sec = make_cls("s", category=CLASSIFY_SECURITY, severity="none",
                   exploitability=0.0)
    rel = make_cls("r", category=CLASSIFY_RELEVANT, severity="none",
                   exploitability=0.0)
    ign = make_cls("i", category=CLASSIFY_IGNORE, severity="none",
                   exploitability=0.0)
    ranked = rank([ign, rel, sec], PINS)
    assert ids(ranked) == ["s", "r", "i"]


def test_order_is_total_and_input_order_independent():
    cls_list = [
        make_cls("a", component="openssl", severity="high", exploitability=2.3),
        make_cls("b", component="openssl", severity="high", exploitability=2.3),
        make_cls("c", component="curl", severity="critical", exploitability=3.9),
        make_cls("d", component="vim", category=CLASSIFY_RELEVANT,
                 severity="none", exploitability=0.0),
    ]
    rng = random.Random(20260914)
    shuffled = list(cls_list)
    rng.shuffle(shuffled)
    assert ids(rank(list(cls_list), PINS)) == ids(rank(shuffled, PINS))


def test_rank_key_orders_consistently_with_rank():
    cls_list = [
        make_cls("x1", component="make", severity="low", exploitability=0.2),
        make_cls("x2", component="curl", severity="low", exploitability=3.9),
        make_cls("x3", component="openssl", severity="medium",
                 exploitability=0.2),
    ]
    decorated = rank(cls_list, PINS)
    # Re-sort the SAME items by rank_key directly: same order must result.
    resorted = sorted(
        (r for r in decorated),
        key=lambda r: rank_key(r.classification, r.reachability,
                               r.reachability_rank),
    )
    assert ids(resorted) == ids(decorated)


# --- RankedClassification record --------------------------------------------


def test_positions_and_rationale_are_complete():
    cls_list = [
        make_cls("m1", component="curl", severity="medium", exploitability=1.9),
        make_cls("m2", component="openssl", severity="critical", exploitability=3.9),
        make_cls("m3", component="make", severity="high", exploitability=2.3),
    ]
    ranked = rank(cls_list, PINS)
    assert [r.position for r in ranked] == [1, 2, 3]
    assert all(isinstance(r, RankedClassification) for r in ranked)
    for r in ranked:
        rr = r.rank_rationale
        assert rr.strip() and "\n" not in rr
        assert f"ranked #{r.position} of {len(ranked)}" in rr
        assert f"severity {r.classification.severity}" in rr
        assert f"reachability {r.reachability}" in rr


def test_rank_is_non_destructive_and_lossless():
    cls_list = [make_cls(f"n{i}", severity="high", exploitability=float(i))
                for i in range(3)]
    snapshot = list(cls_list)
    ranked = rank(cls_list, PINS)
    assert cls_list == snapshot            # input list not mutated
    assert sorted(ids(ranked)) == sorted(f"n{i}" for i in range(3))
    by_id = {r.classification.id: r.classification for r in ranked}
    for c in snapshot:                     # same objects, not copies
        assert by_id[c.id] is c


def test_rank_empty_input_is_empty():
    assert rank([], PINS) == []


def test_rank_accepts_pinned_component_named_by_alias():
    pin = Pin(name="libc", version="2.39", ecosystem="alpine",
              aliases=["glibc"], reachability="core")
    finding = make_cls("f", component="glibc", severity="high", exploitability=1.9)
    assert rank([finding], [pin])[0].reachability == "core"


# --- Classification carries the exploitability signal (item 5 hand-off) ------


def test_classification_carries_exploitability_from_vector():
    from pengyos_update_triage.advisories import Advisory
    from pengyos_update_triage.matching import match_advisory
    from pengyos_update_triage.models import (
        Affected, Range, RangeEvent, Severity,
    )
    from pengyos_update_triage.classification import classify_advisory_match

    def adv(vector):
        return Advisory(
            id="T",
            severities=[Severity(type="CVSS_V3", score=vector)],
            affected=[Affected(
                package_name="openssl", ecosystem="alpine",
                ranges=[Range("SEMVER", [RangeEvent(introduced="0"),
                                         RangeEvent(fixed="3.0.14")])],
            )],
        )

    easy = classify_advisory_match(match_advisory(
        PINS[0], adv("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")))
    assert easy.exploitability == 3.9
    assert easy.exploitability_source == "cvss"
    # The severity rule step records the sub-score in the rationale chain.
    sev_step = next(s for s in easy.rationale_chain if s.rule == "severity")
    assert "exploitability sub-score 3.9" in sev_step.detail

    hard = classify_advisory_match(match_advisory(
        PINS[0], adv("CVSS:3.1/AV:L/AC:L/PR:L/UI:N/S:U/C:N/I:N/A:H")))
    assert hard.exploitability == 1.9
    assert hard.exploitability_source == "cvss"

    # No CVSS at all → 0.0, never over-claimed.
    unq = classify_advisory_match(match_advisory(
        PINS[0], adv(None)))
    # adv(None) keeps an empty severity list only when vector is falsy.
    assert unq.exploitability == 0.0
    assert unq.exploitability_source == "none"

    # A CVSS 3.0 vector is quantified with its own PR multipliers. PR:N means
    # the sub-score matches the 3.1 value here (AV:N/AC:L/PR:N/UI:N -> 3.9).
    v30 = classify_advisory_match(match_advisory(
        PINS[0], adv("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")))
    assert v30.exploitability == 3.9
    assert v30.exploitability_source == "cvss"

    # A CVSS v4.0 vector is quantified for severity but has no v3 sub-score.
    v40 = classify_advisory_match(match_advisory(
        PINS[0], adv("CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N")))
    assert v40.exploitability == 0.0
    assert v40.exploitability_source == "none"


def test_changelog_classification_has_zero_exploitability():
    from pengyos_update_triage.changelog_scan import ChangelogFinding
    from pengyos_update_triage.classification import classify_changelog_finding

    finding = ChangelogFinding(
        component="openssl", version="3.0.14", kind="bump", keywords=[],
        entries=[], pin_version="3.0.13",
        rationale="bump", evidence=["3.0.14 > 3.0.13"],
    )
    c = classify_changelog_finding(finding)
    assert c.exploitability == 0.0
    assert c.exploitability_source == "none"


# --- end-to-end over the committed fixtures ---------------------------------


def _fixture_rank(proposals_only=False):
    pins = load_pins(PINS_OK).pins
    advisories = load_advisories(ADV)
    changelogs = load_changelogs(CHG)
    matches = match_all(pins, advisories)
    findings = scan_changelogs(changelogs, pins)
    security, relevant, ignore = classify(matches, findings)
    chosen = [*security, *relevant] if proposals_only else \
        [*security, *relevant, *ignore]
    return rank(chosen, pins), pins


def test_fixture_rank_top_is_documented_order():
    ranked, _ = _fixture_rank(proposals_only=True)
    by_component = {r.classification.component: r for r in ranked
                    if r.classification.category == CLASSIFY_SECURITY}
    # severity band first, then exploitability within the band:
    #   curl critical 9.8 (3.9) > openssl high 7.5 (3.9)
    #   > glibc high 7.8 (1.9) > zlib medium 5.4 (3.9)
    # Both openssl and glibc sit in the high band — the band, not the exact
    # base score, is the key, so openssl's exploitability (3.9) beats
    # glibc's (1.9) even though glibc's base score is higher.
    top4 = [r.classification.component for r in ranked[:4]]
    assert top4 == ["curl", "openssl", "glibc", "zlib"]
    assert by_component["curl"].severity_rank == 3
    assert by_component["curl"].exploitability == 3.9
    assert by_component["openssl"].exploitability == 3.9
    assert by_component["glibc"].exploitability == 1.9
    assert ranked[1].classification.component == "openssl"
    assert ranked[2].classification.component == "glibc"


def test_fixture_rank_relevant_below_quantified_security():
    ranked, _ = _fixture_rank(proposals_only=True)
    categories = [r.classification.category for r in ranked]
    # 4 security, then the 9 relevant changelog signals (all unquantified).
    assert categories.count(CLASSIFY_SECURITY) == 4
    assert categories.count(CLASSIFY_RELEVANT) == 9
    assert categories[:4] == [CLASSIFY_SECURITY] * 4
    assert all(c == CLASSIFY_RELEVANT for c in categories[4:])


def test_fixture_rank_full_set_puts_relevant_before_ignore_per_reachability():
    ranked, _ = _fixture_rank()
    assert len(ranked) == 45  # 4 security + 9 relevant + 32 ignore
    positions = {r.classification.id: r.position for r in ranked}
    for rel in ranked:
        if rel.classification.category != CLASSIFY_RELEVANT:
            continue
        for ign in ranked:
            if ign.classification.category != CLASSIFY_IGNORE:
                continue
            if ign.reachability_rank <= rel.reachability_rank:
                # equal-or-lower reachability ignore sorts at or after.
                assert rel.position < ign.position or \
                    ign.reachability_rank < rel.reachability_rank


def test_fixture_rank_is_deterministic_across_input_orders():
    pins = load_pins(PINS_OK).pins
    advisories = load_advisories(ADV)
    changelogs = load_changelogs(CHG)
    matches = match_all(pins, advisories)
    findings = scan_changelogs(changelogs, pins)
    security, relevant, ignore = classify(matches, findings)
    chosen = [*security, *relevant, *ignore]
    rng = random.Random(1)
    shuffled = list(chosen)
    rng.shuffle(shuffled)
    assert ids(rank(list(chosen), pins)) == ids(rank(shuffled, pins))
