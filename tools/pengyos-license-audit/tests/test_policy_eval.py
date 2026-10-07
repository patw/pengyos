"""Tests for per-component policy evaluation (``policy_eval``).

These pin the "Implement policy evaluation per component incl. static-vs-dynamic
link flag" subtask. The headline behaviour is that a copyleft claim in the core
is a hard ``FAIL`` when **statically** linked but only a soft ``WARN`` when
**dynamically** linked (the "SPEC §1 note" link flag).
"""

from __future__ import annotations

import pytest

from pengyos_license_audit.model import Pin, Policy, Verdict, VerdictResult
from pengyos_license_audit.policy_eval import evaluate, has_violations


def _pin(
    cid: str,
    license: str,
    tier: str = "leaf",
    link: str = "dynamic",
) -> Pin:
    return Pin(id=cid, license=license, tier=tier, link=link)


# ---------------------------------------------------------------------------
# Verdict enum / dataclass
# ---------------------------------------------------------------------------

def test_verdict_values():
    assert {v.value for v in Verdict} == {"pass", "warn", "fail"}
    assert str(Verdict.PASS) == "pass"


def test_verdict_result_fields():
    r = VerdictResult(
        component_id="x",
        family="permissive",
        verdict=Verdict.PASS,
        reason="ok",
    )
    assert r.component_id == "x"
    assert r.family == "permissive"
    assert r.verdict == Verdict.PASS
    assert r.reason == "ok"


# ---------------------------------------------------------------------------
# Allowed outright
# ---------------------------------------------------------------------------

def test_allowed_family_prefix_passes():
    policy = Policy(allowed_families=["MIT", "Apache-2.0"])
    result = evaluate([_pin("a", "MIT")], policy)[0]
    assert result.verdict is Verdict.PASS
    assert result.reason == "allowed family"


def test_allowed_families_is_prefix_matched_on_claim():
    # The policy prefixes are SPDX-id prefixes matched against the raw claim,
    # not the classified family value.
    policy = Policy(allowed_families=["BSD"])
    assert evaluate([_pin("a", "BSD-3-Clause")], policy)[0].verdict is (
        Verdict.PASS
    )
    # A different family does not match the prefix.
    assert evaluate([_pin("b", "MIT")], policy)[0].verdict is Verdict.FAIL


def test_empty_allowed_families_allows_nothing_outright():
    policy = Policy(allowed_families=[])
    # Even a permissive claim is not *outright* allowed with no declared
    # families (it would only pass via an exception).
    assert evaluate([_pin("a", "MIT")], policy)[0].verdict is Verdict.FAIL


def test_exception_passes_even_for_disallowed_claim():
    policy = Policy(allowed_families=[], exceptions=["blob"])
    result = evaluate([_pin("blob", "Proprietary")], policy)[0]
    assert result.verdict is Verdict.PASS
    assert result.reason == "documented exception"


# ---------------------------------------------------------------------------
# Copyleft in core — the link flag is the deciding factor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family", ["weak-copyleft", "strong-copyleft"])
def test_copyleft_in_core_dynamic_is_warn(family):
    # copyleft_in_core is False and the component is only dynamically linked:
    # the linking exception keeps the downstream binary from being pulled in,
    # so this is a soft flag, not a hard fail.
    claim = "LGPL-3.0-only" if family == "weak-copyleft" else "GPL-3.0-only"
    policy = Policy(allowed_families=[], copyleft_in_core=False)
    result = evaluate([_pin("lib", claim, tier="core", link="dynamic")], policy)[0]
    assert result.verdict is Verdict.WARN
    assert "dynamically linked" in result.reason


@pytest.mark.parametrize("family", ["weak-copyleft", "strong-copyleft"])
def test_copyleft_in_core_static_is_fail(family):
    # copyleft_in_core is False and the component is statically linked into the
    # core: the copyleft code is pulled into the core binary, a hard violation.
    claim = "LGPL-3.0-only" if family == "weak-copyleft" else "GPL-3.0-only"
    policy = Policy(allowed_families=[], copyleft_in_core=False)
    result = evaluate([_pin("lib", claim, tier="core", link="static")], policy)[0]
    assert result.verdict is Verdict.FAIL
    assert "statically linked" in result.reason


def test_copyleft_in_core_allowed_when_copyleft_in_core_true():
    policy = Policy(allowed_families=[], copyleft_in_core=True)
    # Static linking is fine here because copyleft is permitted in core.
    result = evaluate(
        [_pin("lib", "GPL-3.0-only", tier="core", link="static")], policy
    )[0]
    assert result.verdict is Verdict.PASS
    assert result.reason == "copyleft permitted in core"


def test_static_vs_dynamic_distinction_is_the_whole_point():
    # Same claim, same tier, only the link flag differs: dynamic -> warn,
    # static -> fail. This is the SPEC §1 note behaviour.
    policy = Policy(allowed_families=[], copyleft_in_core=False)
    dyn = evaluate(
        [_pin("lib", "GPL-3.0-only", tier="core", link="dynamic")], policy
    )[0]
    stat = evaluate(
        [_pin("lib", "GPL-3.0-only", tier="core", link="static")], policy
    )[0]
    assert dyn.verdict is Verdict.WARN
    assert stat.verdict is Verdict.FAIL


# ---------------------------------------------------------------------------
# Copyleft in a leaf / other disallowed families
# ---------------------------------------------------------------------------

def test_copyleft_in_leaf_not_allowed_is_fail():
    policy = Policy(allowed_families=[])
    result = evaluate([_pin("app", "GPL-3.0-only", tier="leaf")], policy)[0]
    assert result.verdict is Verdict.FAIL


def test_proprietary_in_leaf_not_allowed_is_fail():
    policy = Policy(allowed_families=["MIT"])
    result = evaluate([_pin("fw", "Proprietary", tier="leaf")], policy)[0]
    assert result.verdict is Verdict.FAIL


def test_proprietary_in_core_not_allowed_is_fail():
    policy = Policy(allowed_families=["MIT"])
    result = evaluate([_pin("fw", "Proprietary", tier="core")], policy)[0]
    assert result.verdict is Verdict.FAIL


def test_unknown_in_core_not_allowed_is_fail():
    policy = Policy(allowed_families=["MIT"])
    result = evaluate([_pin("x", "Made-Up-1.0", tier="core")], policy)[0]
    assert result.verdict is Verdict.FAIL
    assert result.family == "unknown"


def test_unknown_in_leaf_not_allowed_is_fail():
    policy = Policy(allowed_families=["MIT"])
    result = evaluate([_pin("x", "Made-Up-1.0", tier="leaf")], policy)[0]
    assert result.verdict is Verdict.FAIL


def test_malformed_claim_is_fail():
    policy = Policy(allowed_families=["MIT"])
    result = evaluate([_pin("x", "MIT OR")], policy)[0]
    assert result.verdict is Verdict.FAIL
    assert "unclassifiable" in result.reason


# ---------------------------------------------------------------------------
# Mixed manifests + has_violations
# ---------------------------------------------------------------------------

def test_order_preserved_and_counts():
    policy = Policy(allowed_families=["MIT"], exceptions=["blob"])
    pins = [
        _pin("a", "MIT"),
        _pin("b", "GPL-3.0-only", tier="core", link="dynamic"),
        _pin("blob", "Proprietary"),
    ]
    results = evaluate(pins, policy)
    assert [r.component_id for r in results] == ["a", "b", "blob"]
    assert [r.verdict for r in results] == [
        Verdict.PASS,
        Verdict.WARN,
        Verdict.PASS,
    ]
    # No hard failures -> no violation.
    assert has_violations(results) is False


def test_has_violations_detects_fail():
    policy = Policy(allowed_families=["MIT"])
    pins = [_pin("a", "MIT"), _pin("b", "Proprietary", tier="leaf")]
    results = evaluate(pins, policy)
    assert has_violations(results) is True


def test_empty_pins():
    policy = Policy(allowed_families=["MIT"])
    assert evaluate([], policy) == []
    assert has_violations([]) is False
