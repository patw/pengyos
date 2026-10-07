"""Tests for proposal emission and the detection pipeline (item 4).

Covers :func:`is_lts`, the :class:`Proposal` value object,
:func:`sort_by_importance`, and the pure :func:`detect_proposals` pipeline.
Detection is exercised against a :class:`~pengyos_upstream_watch.fetcher.MockFetcher`
so no network is touched.

Verifies the spec's required behaviours:

* mock fetcher returning a newer version -> proposal emitted,
* equal / older -> no proposal,
* version comparison edge cases (LTS ``1.2`` vs ``1.2.0``, prereleases).
"""

from __future__ import annotations

import pytest

from pengyos_upstream_watch.compare import Drift
from pengyos_upstream_watch.fetcher import (
    Component,
    MockFetcher,
    UpstreamError,
    UnknownComponentError,
)
from pengyos_upstream_watch.proposals import (
    Proposal,
    detect_proposals,
    is_lts,
    sort_by_importance,
)


# --- is_lts -----------------------------------------------------------------


@pytest.mark.parametrize(
    "version,expected",
    [
        ("1.36", True),      # two-part LTS line
        ("1.2", True),
        ("1.37.0", False),   # three-part regular line
        ("2.39", True),      # two-part LTS-style line
        ("1", False),        # single-part core
    ],
)
def test_is_lts_matches_core_arity(version, expected):
    assert is_lts(version) is expected


def test_is_lts_unknown_version_is_not_lts():
    assert is_lts("not-a-version") is False


# --- Proposal value object --------------------------------------------------


def test_prop_to_dict_shape():
    p = Proposal("busybox", "1.36.1", "1.37.0", False, "https://busybox.net/")
    assert p.to_dict() == {
        "component": "busybox",
        "current": "1.36.1",
        "available": "1.37.0",
        "lts": False,
        "url": "https://busybox.net/",
    }


def test_proposal_to_dict_url_none_is_null():
    p = Proposal("busybox", "1.36.1", "1.37.0", False, None)
    assert p.to_dict()["url"] is None


def test_proposal_is_frozen():
    p = Proposal("busybox", "1.36.1", "1.37.0", False, None)
    with pytest.raises(AttributeError):
        p.component = "x"  # type: ignore[misc]


# --- sort_by_importance -----------------------------------------------------


def test_sort_non_lts_before_lts():
    proposals = [
        Proposal("lts", "1.36", "1.37.0", True, None),
        Proposal("fresh", "1.0", "2.0", False, None),
    ]
    ordered = sort_by_importance(proposals)
    assert [p.component for p in ordered] == ["fresh", "lts"]


def test_sort_larger_jump_first():
    proposals = [
        Proposal("a", "1.0", "1.1", False, None),   # jump of 1 minor
        Proposal("b", "1.0", "2.0", False, None),   # jump of 1 major
    ]
    ordered = sort_by_importance(proposals)
    assert [p.component for p in ordered] == ["b", "a"]


def test_sort_tiebreak_is_name_ascending():
    proposals = [
        Proposal("zeta", "1.0", "1.1", False, None),
        Proposal("alpha", "1.0", "1.1", False, None),
    ]
    ordered = sort_by_importance(proposals)
    assert [p.component for p in ordered] == ["alpha", "zeta"]


def test_sort_is_deterministic_total_order():
    proposals = [
        Proposal("lts-big", "1.36", "1.37.0", True, None),
        Proposal("fresh-big", "1.0", "2.0", False, None),
        Proposal("fresh-small", "1.0", "1.1", False, None),
        Proposal("lts-small", "1.36", "1.36.1", True, None),
    ]
    ordered = sort_by_importance(proposals)
    # non-LTS first (bigger jump first), then LTS (bigger jump first).
    assert [p.component for p in ordered] == [
        "fresh-big",
        "fresh-small",
        "lts-big",
        "lts-small",
    ]


# --- detect_proposals -------------------------------------------------------


def test_detect_newer_emits_proposal():
    fetcher = MockFetcher({"busybox": "1.37.0"})
    proposals = detect_proposals(
        [Component("busybox", "1.36.1", None)], fetcher
    )
    assert len(proposals) == 1
    assert proposals[0].component == "busybox"
    assert proposals[0].current == "1.36.1"
    assert proposals[0].available == "1.37.0"
    assert proposals[0].lts is False


def test_detect_equal_no_proposal():
    fetcher = MockFetcher({"busybox": "1.36.1"})
    assert detect_proposals([Component("busybox", "1.36.1")], fetcher) == []


def test_detect_older_no_proposal():
    # Latest is older than the pin (a backported build): no proposal.
    fetcher = MockFetcher({"busybox": "1.35.0"})
    assert detect_proposals([Component("busybox", "1.36.1")], fetcher) == []


def test_detect_lts_equivalence_no_proposal():
    # LTS x.y vs x.y.z compare equal -> up-to-date -> no proposal.
    fetcher = MockFetcher({"busybox": "1.36.0"})
    assert detect_proposals([Component("busybox", "1.36")], fetcher) == []


def test_detect_lts_flag_set_for_two_part():
    fetcher = MockFetcher({"busybox": "1.37.0"})
    (proposal,) = detect_proposals([Component("busybox", "1.36")], fetcher)
    assert proposal.lts is True


def test_detect_carrys_url():
    fetcher = MockFetcher({"busybox": "1.37.0"})
    (proposal,) = detect_proposals(
        [Component("busybox", "1.36.1", "https://busybox.net/")], fetcher
    )
    assert proposal.url == "https://busybox.net/"


def test_detect_skips_component_without_version():
    # A component with no current version cannot be compared.
    fetcher = MockFetcher({"busybox": "1.37.0"})
    assert detect_proposals([Component("busybox", None)], fetcher) == []


def test_detect_mixed_components_only_drifted_emitted():
    fetcher = MockFetcher({"busybox": "1.37.0", "glibc": "2.39"})
    components = [
        Component("busybox", "1.36.1", None),
        Component("glibc", "2.39", None),  # up-to-date
    ]
    proposals = detect_proposals(components, fetcher)
    assert [p.component for p in proposals] == ["busybox"]


def test_detect_prerelease_edge_case():
    # 1.36.1-rc1 (a prerelease) is older than 1.36.1 stable -> drift.
    fetcher = MockFetcher({"busybox": "1.36.1"})
    (proposal,) = detect_proposals([Component("busybox", "1.36.1-rc1")], fetcher)
    assert proposal.available == "1.36.1"


def test_detect_sorts_by_importance():
    fetcher = MockFetcher({"alpha": "2.0", "beta": "1.1"})
    components = [
        Component("beta", "1.0", None),
        Component("alpha", "1.0", None),
    ]
    proposals = detect_proposals(components, fetcher)
    # Both non-LTS; equal jump (1.0 -> 2.0 vs 1.0 -> 1.1 -> alpha jumps more).
    assert [p.component for p in proposals] == ["alpha", "beta"]


def test_detect_propagates_fetcher_error():
    # An unknown component raises UnknownComponentError (a FetcherError).
    fetcher = MockFetcher()  # empty: no answer for "busybox"
    with pytest.raises(UnknownComponentError):
        detect_proposals([Component("busybox", "1.36.1")], fetcher)


def test_detect_propagates_upstream_error():
    fetcher = MockFetcher({"busybox": "1.37.0"})

    def boom(component):
        raise UpstreamError("unreachable")

    fetcher.latest_version = boom  # type: ignore[method-assign]
    with pytest.raises(UpstreamError):
        detect_proposals([Component("busybox", "1.36.1")], fetcher)


def test_detect_lts_override():
    # A custom lts_of callable can be injected for testing. The pin must
    # actually drift (1.36.0 -> 1.37.0) for a proposal to be emitted.
    fetcher = MockFetcher({"busybox": "1.37.0"})
    (proposal,) = detect_proposals(
        [Component("busybox", "1.36.0")], fetcher, lts_of=lambda v: True
    )
    assert proposal.lts is True


def test_detect_detects_drift_class_matches():
    # Sanity: a proposal is only emitted when classify says DRIFT.
    fetcher = MockFetcher({"busybox": "1.37.0"})
    components = [Component("busybox", "1.36.1")]
    # classify(current, latest) must be DRIFT for the single pin.
    from pengyos_upstream_watch.compare import classify

    assert classify("1.36.1", "1.37.0") is Drift.DRIFT
    (proposal,) = detect_proposals(components, fetcher)
    assert proposal.available == "1.37.0"
