"""Tests for the gate submit client.

The HTTP layer is monkeypatched so the exact request body is captured without
touching the network — this is the "submit builds the correct request (mock)"
verification.
"""

from __future__ import annotations

import pytest

from pengyos_propose.gate import GateClient, build_payload
from pengyos_propose.gating import GateError
from pengyos_propose.proposal import Proposal, TIER_1, TIER_2


def fake_transport(url, payload):
    """Capture the request and return a canned 200 response."""
    fake_transport.calls.append((url, payload))
    return 200, {"ok": True, "proposal_id": "abc123", "review_url": "http://gate/r/xyz"}


@pytest.fixture
def captured():
    fake_transport.calls = []
    return fake_transport


def test_build_payload_spreads_fields_and_sets_controls(sample_proposal):
    payload = build_payload(sample_proposal, human_approved=True)
    assert payload["title"] == "Bump systemd in the base image"
    assert payload["component"] == "systemd"
    assert payload["from"] == "systemd 256"  # from_ serialized as "from"
    assert payload["human_approved"] is True
    assert payload["client"] == "pengyos-propose"


def test_build_payload_defaults_human_approved_false(sample_proposal):
    payload = build_payload(sample_proposal)
    assert payload["human_approved"] is False


def test_submit_posts_captured_request(captured, sample_proposal):
    client = GateClient("http://gate:8071", transport=captured)
    resp = client.submit(sample_proposal, human_approved=True)

    assert client.url == "http://gate:8071/api/propose"
    assert resp.ok is True
    assert resp.proposal_id == "abc123"
    assert resp.review_url == "http://gate/r/xyz"

    url, payload = captured.calls[0]
    assert url == "http://gate:8071/api/propose"
    assert payload["human_approved"] is True


def test_submit_non_2xx_is_not_ok(captured, sample_proposal):
    def refusing_transport(url, payload):
        captured.calls.append((url, payload))
        return 403, {"ok": False, "error": "forbidden"}

    client = GateClient("http://gate", transport=refusing_transport)
    resp = client.submit(sample_proposal)
    assert resp.ok is False
    assert resp.status_code == 403
    assert resp.body["error"] == "forbidden"


def test_tier2_forwards_flag(captured):
    p = Proposal(title="t", tier=TIER_2)
    client = GateClient("http://gate", transport=captured)
    client.submit(p, human_approved=True)
    assert captured.calls[0][1]["tier"] == TIER_2
    assert captured.calls[0][1]["human_approved"] is True


def test_tier2_without_human_flag_is_refused(captured):
    """A Tier 2 without explicit approval is refused and never sent."""
    p = Proposal(title="t", tier=TIER_2)
    client = GateClient("http://gate", transport=captured)

    with pytest.raises(GateError):
        client.submit(p)  # human_approved defaults to False

    # The refusal happens before any request — the transport is never called.
    assert captured.calls == []


def test_tier2_with_human_flag_is_sent(captured):
    """A Tier 2 with explicit approval flows through to the gate."""
    p = Proposal(title="t", tier=TIER_2)
    client = GateClient("http://gate", transport=captured)

    resp = client.submit(p, human_approved=True)

    assert resp.ok is True
    assert len(captured.calls) == 1
    url, payload = captured.calls[0]
    assert url == "http://gate/api/propose"
    assert payload["tier"] == TIER_2
    assert payload["human_approved"] is True


def test_tier1_without_human_flag_is_sent(captured):
    """Tier 1 (agent-mutable) does not require the human-approval flag."""
    p = Proposal(title="t", tier=TIER_1)
    client = GateClient("http://gate", transport=captured)

    resp = client.submit(p)

    assert resp.ok is True
    assert len(captured.calls) == 1


def test_tier0_without_human_flag_is_sent(captured):
    """Tier 0 (image/baseline) does not require the human-approval flag."""
    from pengyos_propose.proposal import TIER_0

    p = Proposal(title="t", tier=TIER_0)
    client = GateClient("http://gate", transport=captured)

    resp = client.submit(p)

    assert resp.ok is True
    assert len(captured.calls) == 1
