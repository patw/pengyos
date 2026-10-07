"""Transition-state probe for the pengy-agent's model-server port pin.

The follow-up "pin the local model server's port in the
pengy-agent network grant" is blocked until PengyOS defines the model
server's default port upstream, and the port must never be guessed
(iteration-6 rule). The write-side of that blocked item is fully
automated: :mod:`pengyos_apparmor.portdiscovery` re-checks upstream, and
:mod:`pengyos_apparmor.portpin` plans and renders the mechanical
transition. What this module adds is the *read side*: given a repo,
*where is the transition right now?*

:func:`inspect_transition` inspects the four transition files
(:data:`portpin.PORT_PIN_TRANSITION_FILES`) and classifies each one:

* ``unbound`` — the file still carries the pre-transition anchor line
  (the port-unbound grant, the matching ``SANCTIONED_NETWORK_RULES``
  entry, the two test pins);
* ``pinned:<N>`` — the file carries the post-transition form for port
  *N* (exactly the strings the rendered patch would write);
* ``drifted`` — anything else: a missing anchor, both forms present, a
  port range (the planner only ever emits single ports), a port outside
  ``1..65535``, or files pinned to *different* ports.

The overall state then is:

* ``blocked``  — every file unbound and the upstream scan found no
  model-server port definition (the item stays blocked);
* ``ready``    — every file unbound and the scan found candidate(s)
  (the mechanical transition can now be run:
  ``plan-port-pin <N> --patch``);
* ``pinned``   — every file consistently pinned to the same valid port
  (the transition is complete and internally consistent; the upstream
  scan is reported as evidence — the command never decides the port);
* ``partial``  — a mixed or drifted repo: some files already carry the
  pinned form and some do not (or any file has drifted). A half-applied
  transition is exactly what this state exists to catch — nothing else
  in the tool reports it until a full test run.

Nothing here modifies files and nothing here guesses a port: the probe
only *reports* where things stand, for a human to act on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, Union

from . import portdiscovery, portpin
from .portdiscovery import PortCandidate
from .portpin import PortPinTransitionError

__all__ = [
    "STATE_BLOCKED",
    "STATE_READY",
    "STATE_PARTIAL",
    "STATE_PINNED",
    "COMMITABLE",
    "NOT_YET_COMMITABLE",
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_BAD_INPUT",
    "EXIT_BLOCKED",
    "EXIT_NOT_YET_COMMITABLE",
    "FORM_UNBOUND",
    "FORM_PINNED",
    "FORM_DRIFTED",
    "PortStatusError",
    "FileState",
    "PortPinStatus",
    "inspect_transition",
]

STATE_BLOCKED = "blocked"
STATE_READY = "ready"
STATE_PARTIAL = "partial"
STATE_PINNED = "pinned"

#: Commit-ability verdicts for a ``pinned`` repo. ``COMMITABLE`` is the
#: terminal state: every transition file is consistent, strict-clean,
#: *and* the pinned port is defined upstream (it appears among the
#: upstream scan's candidates) — the pin is PengyOS's decision and may be
#: committed and the checklist item flipped. ``NOT_YET_COMMITABLE`` is
#: the same internally-consistent pinned repo where the pinned port is
#: NOT yet defined upstream: the pin is a local, not-yet-ratified decision
#: and must not be committed yet (see the runbook's commit step).
COMMITABLE = "commit-able"
NOT_YET_COMMITABLE = "not-yet-commit-able"

#: The ``port-pin-status`` CLI exit codes, as a single source of truth so
#: the runbook / spec lockstep tests and the CLI read the same numbers.
EXIT_OK = 0  # ready, or pinned AND commit-able (upstream-defined)
EXIT_PARTIAL = 1  # mixed/drifted repo (reconcile the files)
EXIT_BAD_INPUT = 2  # a transition file or scan root is missing
EXIT_BLOCKED = 3  # all unbound and no upstream definition (still blocked)
EXIT_NOT_YET_COMMITABLE = 4  # pinned, but the pinned port is not defined upstream yet

FORM_UNBOUND = "unbound"
FORM_PINNED = "pinned"
FORM_DRIFTED = "drifted"

#: Per-file anchor descriptions (used in the probe's own error messages
#: and in test pinning — they name *what* each file's anchor is).
_FILE_LABELS = {
    "profiles/pengy-agent": "the model-server grant line",
    "pengyos_apparmor/validator.py": "the SANCTIONED_NETWORK_RULES entry",
    "tests/test_profiles.py": "the test_network_grants pin",
    "tests/test_validator.py": "the unbound-form anchor",
}

#: The post-transition form of the ``SANCTIONED_NETWORK_RULES`` entry —
#: one exact rule per line inside the frozenset, trailing comma included.
_SET_ENTRY_RE = re.compile(
    r'^"network inet tcp peer=\(ip=127\.0\.0\.1(?: port=(?P<tok>\d+(?:-\d+)?))?\)",$'
)
#: The post-transition form of the ``test_network_grants`` pin.
_TEST_PIN_RE = re.compile(
    r'^expected = \{"pengy-agent": \["network inet tcp peer='
    r"\(ip=127\.0\.0\.1(?: port=(?P<tok>\d+(?:-\d+)?))?\)\"\]\}$"
)
#: The ``test_validator`` anchor line: unbound when no argument is
#: passed, port-pinned when a decimal port literal is.
_ANCHOR_RULE_RE = re.compile(r'^rule = _model_server_rule\((?:"(?P<tok>\d+(?:-\d+)?)")?\)$')
#: The companion assert line in ``test_validator``: unbound form asserts
#: the grant carries no ``port=``, pinned form asserts the pinned port.
_ANCHOR_ASSERT_RE = re.compile(r'^assert (?:f?)"port=(?P<tok>\d+)" in rule$')
_UNBOUND_ASSERT = 'assert "port=" not in rule'


class PortStatusError(Exception):
    """A transition file is missing under the repo root (bad input)."""


@dataclass(frozen=True)
class FileState:
    """The classified state of one transition file.

    ``form`` is one of :data:`FORM_UNBOUND` / :data:`FORM_PINNED` /
    :data:`FORM_DRIFTED`; ``port`` is set (valid, 1..65535) only for
    :data:`FORM_PINNED`; ``detail`` names the anchor and any drift
    reason, for human-readable reports.
    """

    rel: str
    form: str
    port: Optional[int]
    detail: str


@dataclass(frozen=True)
class PortPinStatus:
    """The overall state of the port-pin transition in one repo.

    ``state`` is one of :data:`STATE_BLOCKED` / :data:`STATE_READY` /
    :data:`STATE_PARTIAL` / :data:`STATE_PINNED`. ``files`` holds one
    :class:`FileState` per transition file (in the fixed
    ``PORT_PIN_TRANSITION_FILES`` order); ``candidates`` the upstream
    scan results; ``port`` the consistently pinned port when the state
    is :data:`STATE_PINNED` (else ``None``).

    :attr:`commitable` is the *commit-ability* verdict the runbook's
    final step needs: a ``pinned`` repo is commit-able (and the
    checklist item may be flipped) only when the pinned port is defined
    upstream — it appears among ``candidates``. A pinned repo whose port
    is not yet upstream is ``pinned`` but *not-yet-commit-able*: the pin
    is a local, not-yet-ratified decision. Every non-``pinned`` state
    (``blocked`` / ``ready`` / ``partial``) is not commit-able either.
    """

    state: str
    files: Tuple[FileState, ...]
    candidates: Tuple[PortCandidate, ...]
    port: Optional[int]

    @property
    def commitable(self) -> bool:
        """True iff the state is ``pinned`` AND the pinned port is defined
        upstream (present among the upstream scan's candidates). The
        other half of a ``pinned`` repo — one whose port is not yet
        upstream — is not commit-able (the runbook's commit step is
        gated on this)."""
        if self.state != STATE_PINNED or self.port is None:
            return False
        return self.port in {c.port for c in self.candidates}

    def commit_ability(self) -> str:
        """:data:`COMMITABLE` / :data:`NOT_YET_COMMITABLE` when
        :attr:`state` is :data:`STATE_PINNED`; ``None`` for every other
        state (commit-ability is only meaningful once the repo is
        internally consistent)."""
        if self.state != STATE_PINNED:
            return None
        return COMMITABLE if self.commitable else NOT_YET_COMMITABLE


def _validate_port(token: str) -> Tuple[Optional[int], Optional[str]]:
    """``(port, None)`` for a valid single port token, else
    ``(None, reason)`` (ranges and out-of-range numbers are drift)."""
    if "-" in token:
        return None, f"port range {token!r} (the planner only emits single ports)"
    try:
        return portpin.normalize_port(token), None
    except PortPinTransitionError as exc:
        return None, f"invalid port {token!r} ({exc})"


def _count_forms(unbound_count: int, pinned_count: int, pinned_port, reason) -> Tuple[str, Optional[int], str]:
    """Shared classification: exactly one unbound anchor -> unbound;
    exactly one pinned anchor (all the same valid port) -> pinned;
    anything else -> drifted."""
    if unbound_count == 1 and pinned_count == 0:
        return FORM_UNBOUND, None, "unbound anchor present"
    if unbound_count == 0 and pinned_count == 1 and reason is None:
        return FORM_PINNED, pinned_port, f"port-pinned anchor present (port {pinned_port})"
    if unbound_count > 0 and pinned_count > 0:
        return FORM_DRIFTED, None, "carries both the unbound and a port-pinned anchor"
    if reason is not None:
        return FORM_DRIFTED, None, reason
    if pinned_count > 1:
        return FORM_DRIFTED, None, "carries the pinned anchor more than once"
    if unbound_count > 1:
        return FORM_DRIFTED, None, "carries the unbound anchor more than once"
    return FORM_DRIFTED, None, "carries neither the unbound nor a recognizable port-pinned anchor"


def _profile_form(text: str) -> Tuple[str, Optional[int], str]:
    unbound_count = 0
    pinned_count = 0
    port: Optional[int] = None
    reason: Optional[str] = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped in (
            portpin.UNBOUND_MODEL_SERVER_RULE,
            portpin.UNBOUND_MODEL_SERVER_RULE + ",",
        ):
            unbound_count += 1
            continue
        m = portpin.PINNED_RULE_RE.match(stripped)
        if m:
            pinned_count += 1
            n, why = _validate_port(m.group(1))
            if why is not None:
                reason = reason or why
            elif port is None:
                port = n
            elif port != n:
                reason = reason or f"pins conflicting ports {port} and {n}"
    return _count_forms(unbound_count, pinned_count, port, reason)


def _set_entry_form(text: str) -> Tuple[str, Optional[int], str]:
    unbound_count = 0
    pinned_count = 0
    port: Optional[int] = None
    reason: Optional[str] = None
    for raw in text.splitlines():
        m = _SET_ENTRY_RE.match(raw.strip())
        if not m:
            continue
        if m.group("tok") is None:
            unbound_count += 1
        else:
            pinned_count += 1
            n, why = _validate_port(m.group("tok"))
            if why is not None:
                reason = reason or why
            elif port is None:
                port = n
            elif port != n:
                reason = reason or f"pins conflicting ports {port} and {n}"
    return _count_forms(unbound_count, pinned_count, port, reason)


def _test_pin_form(text: str) -> Tuple[str, Optional[int], str]:
    unbound_count = 0
    pinned_count = 0
    port: Optional[int] = None
    reason: Optional[str] = None
    for raw in text.splitlines():
        m = _TEST_PIN_RE.match(raw.strip())
        if not m:
            continue
        if m.group("tok") is None:
            unbound_count += 1
        else:
            pinned_count += 1
            n, why = _validate_port(m.group("tok"))
            if why is not None:
                reason = reason or why
            elif port is None:
                port = n
            elif port != n:
                reason = reason or f"pins conflicting ports {port} and {n}"
    return _count_forms(unbound_count, pinned_count, port, reason)


def _anchor_form(text: str) -> Tuple[str, Optional[int], str]:
    unbound_count = 0
    pinned_count = 0
    port: Optional[int] = None
    reason: Optional[str] = None
    unbound_asserts = 0
    pinned_asserts = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        m = _ANCHOR_RULE_RE.match(stripped)
        if m:
            if m.group("tok") is None:
                unbound_count += 1
            else:
                pinned_count += 1
                n, why = _validate_port(m.group("tok"))
                if why is not None:
                    reason = reason or why
                elif port is None:
                    port = n
                elif port != n:
                    reason = reason or f"pins conflicting ports {port} and {n}"
            continue
        if stripped == _UNBOUND_ASSERT:
            unbound_asserts += 1
        am = _ANCHOR_ASSERT_RE.match(stripped)
        if am:
            pinned_asserts += 1
    if unbound_count == 1 and unbound_asserts == 0:
        reason = reason or "the anchor line was flipped but its companion assert was not"
    if pinned_count == 1 and pinned_asserts == 0:
        reason = reason or "the anchor line was flipped but its companion assert was not"
    if unbound_asserts > 1 or pinned_asserts > 1:
        reason = reason or "the companion assert occurs more than once"
    return _count_forms(unbound_count, pinned_count, port, reason)


_FORM_FNS = {
    "profiles/pengy-agent": _profile_form,
    "pengyos_apparmor/validator.py": _set_entry_form,
    "tests/test_profiles.py": _test_pin_form,
    "tests/test_validator.py": _anchor_form,
}


def _file_state(rel: str, text: str) -> FileState:
    form, port, why = _FORM_FNS[rel](text)
    detail = f"{_FILE_LABELS[rel]}: {why}"
    return FileState(rel=rel, form=form, port=port, detail=detail)


def inspect_transition(
    repo_root: Union[str, Path],
    scan_root: Union[str, Path] = None,
) -> PortPinStatus:
    """Classify the port-pin transition state of the repo under
    *repo_root* and cross-check it against the upstream port scan under
    *scan_root* (default: :data:`portdiscovery.DEFAULT_SCAN_ROOT`).

    Raises :class:`PortStatusError` when any of the four transition
    files is missing under *repo_root* (bad input — the CLI exits 2).
    Read-only: never writes, never guesses a port.
    """
    root = Path(repo_root)
    files: list[FileState] = []
    for rel in portpin.PORT_PIN_TRANSITION_FILES:
        path = root / rel
        if not path.is_file():
            raise PortStatusError(f"transition file missing under {root}: {rel}")
        files.append(_file_state(rel, path.read_text(encoding="utf-8")))

    forms = [f.form for f in files]
    ports = {f.port for f in files}

    candidates = tuple(
        portdiscovery.find_model_server_port(
            scan_root if scan_root is not None else portdiscovery.DEFAULT_SCAN_ROOT
        )
    )

    if all(f == FORM_UNBOUND for f in forms):
        state = STATE_READY if candidates else STATE_BLOCKED
        port = None
    elif all(f == FORM_PINNED for f in forms) and len(ports) == 1:
        state = STATE_PINNED
        port = ports.pop()
    else:
        state = STATE_PARTIAL
        port = None
    return PortPinStatus(state=state, files=tuple(files), candidates=candidates, port=port)
