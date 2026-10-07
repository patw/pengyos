"""Tests for the proposal schema and loading."""

from __future__ import annotations

import json

import pytest

from pengyos_propose.proposal import (
    Proposal,
    load_proposal,
    load_proposal_file,
    tier_label,
    HUMAN_USABLE_FIELDS,
    TIER_0,
    TIER_1,
    TIER_2,
    VALID_TIERS,
)


def test_load_from_sample_file(sample_proposal):
    assert sample_proposal.title == "Bump systemd in the base image"
    assert sample_proposal.component == "systemd"
    assert sample_proposal.from_ == "systemd 256"
    assert sample_proposal.to == "systemd 257"
    assert sample_proposal.tier == TIER_1
    assert not sample_proposal.is_tier2()


def test_load_from_tier2_file(tier2_proposal_path):
    p = load_proposal_file(tier2_proposal_path)
    assert p.tier == TIER_2
    assert p.is_tier2()


def test_from_dict_rejects_empty_proposal():
    """A proposal with no human-usable field is rejected at load."""
    with pytest.raises(ValueError, match="human-usable"):
        Proposal.from_dict({})


def test_from_dict_defaults_present_fields():
    """A single present field is enough; the rest fall back to defaults."""
    p = Proposal.from_dict({"title": "only a title"})
    assert p.title == "only a title"
    assert p.component == ""
    assert p.tier == TIER_1  # defaults when absent
    assert p.evidence == []


@pytest.mark.parametrize(
    "payload",
    [{"title": "t"}, {"component": "c"}, {"tier": TIER_1}],
)
def test_from_dict_accepts_any_single_human_field(payload):
    """Any one of title/component/tier makes the proposal loadable."""
    p = Proposal.from_dict(payload)
    assert p is not None


def test_from_dict_rejects_blank_human_fields():
    """Blank (whitespace-only) fields count as missing."""
    with pytest.raises(ValueError, match="human-usable"):
        Proposal.from_dict({"title": "   ", "component": "", "tier": ""})


def test_from_dict_blank_tier_is_missing_without_others():
    """A blank tier with no title/component is rejected (tier presence != valid)."""
    with pytest.raises(ValueError, match="human-usable"):
        Proposal.from_dict({"tier": ""})


def test_from_dict_present_tier_satisfies_required_fields():
    """A present (non-empty) tier satisfies the required-fields check even before
    strict tier *validity* is enforced."""
    p = Proposal.from_dict({"tier": TIER_2})
    assert p.is_tier2()


def test_load_proposal_rejects_empty():
    with pytest.raises(ValueError, match="human-usable"):
        load_proposal("{}")


def test_human_usable_fields_constant():
    assert tuple(HUMAN_USABLE_FIELDS) == ("title", "component", "tier")


def test_from_dict_invalid_tier_falls_back_to_tier1():
    p = Proposal.from_dict({"tier": "banana"})
    assert p.tier == TIER_1


def test_from_dict_accepts_string_evidence():
    # Needs a human-usable field to load; the point is evidence coercion.
    p = Proposal.from_dict({"title": "t", "evidence": "only one"})
    assert p.evidence == ["only one"]


def test_from_dict_rejects_non_object():
    with pytest.raises(ValueError):
        Proposal.from_dict(["not", "an", "object"])


def test_from_dict_rejects_bad_json():
    with pytest.raises(ValueError):
        load_proposal("{not json")


def test_to_dict_renames_from_to_from():
    p = Proposal(title="t", from_="a", to="b")
    d = p.to_dict()
    assert d["from"] == "a"
    assert "from_" not in d


def test_round_trip_roundtrips(sample_proposal):
    d = sample_proposal.to_dict()
    p2 = Proposal.from_dict(d)
    assert p2.to_dict() == d


def test_tier_labels():
    assert tier_label(TIER_2) == "Tier 2 (human-signed)"
    assert tier_label(TIER_0) == "Tier 0 (image)"
    assert tier_label("unknown") == "unknown"


def test_valid_tiers_constant():
    assert set(VALID_TIERS) == {TIER_0, TIER_1, TIER_2}
