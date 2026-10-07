"""Per-component policy evaluation for the PengyOS license-audit tool.

Given a pins manifest and a :class:`~pengyos_license_audit.model.Policy`, this
module decides, for every component, whether the license claim is allowed.

The verdicts (see :class:`.Verdict`) are:

* ``pass`` — allowed outright: the claim starts with a declared
  ``allowed_families`` prefix, or copyleft is permitted in the core
  (``copyleft_in_core``), or the component id is a documented exception.
* ``warn`` — *flagged*: not outright allowed, but the concern is soft enough to
  warn rather than fail. The canonical case is a copyleft claim in the core
  while ``copyleft_in_core`` is false **and** the component is only
  **dynamically** linked — the freedom concern is real, but the linking
  exception keeps the downstream binary from being pulled in.
* ``fail`` — a hard violation: a copyleft claim **statically** linked into the
  core (static linking of copyleft into core is the thing the policy exists to
  catch), a proprietary/unknown claim in the core, or any other disallowed
  family that is not a soft-flaggable copyleft-in-core case.

The **static-vs-dynamic link flag** (``Pin.link``) is what separates ``warn``
from ``fail`` for a copyleft-in-core claim: static linking pulls the copyleft
code into the core binary and is treated as a hard violation, while dynamic
linking leaves the linked code separate and is only flagged. This is the
"SPEC §1 note" behaviour the policy-evaluation subtask exists to implement.
"""

from __future__ import annotations

from typing import Sequence

from .classification import classify
from .errors import ValidationError
from .families import LicenseFamily
from .model import Pin, Policy, Verdict, VerdictResult

# Families that are copyleft: weak or strong. These are the only families whose
# core-tier presence can be a *soft* (warn) flag rather than a hard fail.
_COPYLEFT_FAMILIES = frozenset(
    {
        LicenseFamily.WEAK_COPYLEFT,
        LicenseFamily.STRONG_COPYLEFT,
    }
)


def _allowed_prefix(policy: Policy, claim: str) -> bool:
    """Return ``True`` if *claim* starts with a declared allowed prefix.

    ``allowed_families`` is a list of SPDX-id *prefixes* (e.g. ``"MIT"``), so a
    component is allowed outright when its *claim* starts with one of them. An
    empty ``allowed_families`` list allows nothing outright.
    """
    if not policy.allowed_families:
        return False
    return any(claim.startswith(prefix) for prefix in policy.allowed_families)


def _evaluate_pin(pin: Pin, policy: Policy) -> VerdictResult:
    """Evaluate a single *pin* against *policy* and return its verdict."""
    try:
        family_value = classify(pin.license).value
    except ValidationError as exc:
        # A malformed claim is a manifest problem, surfaced as a hard fail so
        # the audit reports it rather than silently passing.
        return VerdictResult(
            component_id=pin.id,
            family="unknown",
            verdict=Verdict.FAIL,
            reason=f"unclassifiable license claim: {exc}",
        )

    family = LicenseFamily(family_value)

    # 1. Documented exceptions are exempt from the policy entirely.
    if policy.is_exception(pin.id):
        return VerdictResult(
            component_id=pin.id,
            family=family_value,
            verdict=Verdict.PASS,
            reason="documented exception",
        )

    # 2. Allowed outright: the claim starts with a declared allowed prefix.
    if _allowed_prefix(policy, pin.license):
        return VerdictResult(
            component_id=pin.id,
            family=family_value,
            verdict=Verdict.PASS,
            reason="allowed family",
        )

    # 3. Copyleft in the core.
    if family in _COPYLEFT_FAMILIES and pin.is_core:
        if policy.copyleft_in_core:
            return VerdictResult(
                component_id=pin.id,
                family=family_value,
                verdict=Verdict.PASS,
                reason="copyleft permitted in core",
            )
        # copyleft_in_core is False: the link flag decides warn vs. fail.
        if pin.links_static:
            return VerdictResult(
                component_id=pin.id,
                family=family_value,
                verdict=Verdict.FAIL,
                reason=(
                    f"{family_value} copyleft statically linked into core "
                    "(static linking pulls the code into the core binary)"
                ),
            )
        return VerdictResult(
            component_id=pin.id,
            family=family_value,
            verdict=Verdict.WARN,
            reason=(
                f"{family_value} copyleft in core, dynamically linked; "
                "flagged (set copyleft_in_core=true to allow)"
            ),
        )

    # 4. Copyleft in a leaf: leaves follow allowed_families (already handled by
    #    step 2), so a disallowed copyleft leaf is a hard fail.
    if family in _COPYLEFT_FAMILIES:
        return VerdictResult(
            component_id=pin.id,
            family=family_value,
            verdict=Verdict.FAIL,
            reason=f"{family_value} copyleft is not an allowed family",
        )

    # 5. Anything else that is not outright allowed (proprietary, unknown, ...).
    return VerdictResult(
        component_id=pin.id,
        family=family_value,
        verdict=Verdict.FAIL,
        reason=f"{family_value} is not an allowed family",
    )


def evaluate(pins: Sequence[Pin], policy: Policy) -> list[VerdictResult]:
    """Evaluate every *pin* against *policy*, preserving manifest order.

    Raises :class:`ValidationError` only if a claim is malformed *and* the
    caller wants strict failure; by default a malformed claim is reported as a
    hard ``FAIL`` for that component (see :func:`_evaluate_pin`).
    """
    return [_evaluate_pin(pin, policy) for pin in pins]


def has_violations(results: Sequence[VerdictResult]) -> bool:
    """Return ``True`` if any component was flagged ``FAIL``."""
    return any(r.verdict == Verdict.FAIL for r in results)
