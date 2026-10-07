"""Submit proposals to the propose/approve gate.

The gate is an HTTP service (see the `propose` skill) that a human reviews
before anything is built.  This module is the *client*: it builds the request
body from a :class:`~pengyos_propose.proposal.Proposal` and POSTs it.

The network call is isolated in :func:`_http_post` so tests can monkeypatch it
to capture the exact request without touching the network — the "submit builds
the correct request (mock)" verification lives here.

Tier gating is enforced *here*, at the edge of the submit path: a Tier 2
(human-signed) proposal is refused with :class:`~pengyos_propose.gating.GateError`
**before any request is sent** unless an explicit ``human_approved`` flag is set.
The pure predicate lives in :mod:`pengyos_propose.gating`; the enforcement that
wires it into the request path is this module's responsibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .gating import GateError, needs_human_approval
from .proposal import Proposal

# A transport is just "take (url, payload_dict) -> (status_code, parsed_body)".
# Tests monkeypatch this to avoid the network; integration tests point it at a
# real gate behind PENGYOS_IT=1.
Transport = Callable[[str, dict[str, Any]], tuple[int, dict[str, Any]]]

API_PATH = "/api/propose"


@dataclass
class GateResponse:
    """Normalized result of a submit attempt."""

    ok: bool
    status_code: int
    body: dict[str, Any]

    @property
    def proposal_id(self) -> Optional[str]:
        return self.body.get("proposal_id")

    @property
    def review_url(self) -> Optional[str]:
        return self.body.get("review_url")


def build_payload(
    p: Proposal,
    *,
    human_approved: bool = False,
    client: str = "pengyos-propose",
) -> dict[str, Any]:
    """Build the JSON body the gate expects for a proposal.

    The proposal's own fields are spread in, and the gate-facing control fields
    (``human_approved``, ``client``) are added.  ``from_`` is serialized as
    ``from`` per the schema.
    """
    body = p.to_dict()
    body["human_approved"] = bool(human_approved)
    body["client"] = client
    return body


def _http_post(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Perform the HTTP POST.

    Imported as a module-level symbol so tests can monkeypatch
    ``pengyos_propose.gate._http_post`` to capture the request offline.  Real
    integration uses ``urllib``; the indirection keeps the client testable.
    """
    import urllib.error
    import urllib.request

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:  # pragma: no cover - network path
        raw = e.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": raw}
        return e.code, parsed


class GateClient:
    """Thin HTTP client for the propose/approve gate."""

    def __init__(self, base_url: str, *, transport: Optional[Transport] = None):
        self.base_url = base_url.rstrip("/")
        # transport defaults to the real HTTP POST; tests inject a fake.
        self._transport: Transport = transport or _http_post

    @property
    def url(self) -> str:
        return self.base_url + API_PATH

    def submit(
        self,
        p: Proposal,
        *,
        human_approved: bool = False,
        client: str = "pengyos-propose",
    ) -> GateResponse:
        """Submit a proposal to the gate.

        Enforces the human gate at the edge: a Tier 2 (human-signed) proposal is
        refused with :class:`~pengyos_propose.gating.GateError` **before any
        request is sent** unless ``human_approved`` is set. This is the client
        side of "cannot bypass the human gate for Tier 2" — the same refusal the
        gate server applies.

        Returns a normalized :class:`GateResponse` on success.
        """
        if needs_human_approval(p) and not human_approved:
            raise GateError(
                "tier2 proposal requires explicit human approval "
                "(submit with --human-approved)"
            )
        payload = build_payload(p, human_approved=human_approved, client=client)
        status_code, body = self._transport(self.url, payload)
        ok = 200 <= status_code < 300
        return GateResponse(ok=ok, status_code=status_code, body=body)
