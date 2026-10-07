"""Tests for policy verification (``pengyos_measured_boot.policy``).

No real ``swtpm`` is invoked: policy parsing and verification operate on strings
and in-memory mappings, so these tests need no root, hardware, or network.
"""

from __future__ import annotations

import json

import pytest

from pengyos_measured_boot.errors import (
    Mismatch,
    PengyOSMeasuredBootError,
    PolicyMismatch,
)
from pengyos_measured_boot.pcrs import Pcrs, parse_pcrs, parse_pcrs_file
from pengyos_measured_boot.policy import Policy, load_policy, parse_policy


def test_policy_from_mapping_builds_policy() -> None:
    policy = Policy.from_mapping({0: 0, 7: 0xDEADBEEF})
    assert policy == Policy({0: 0, 7: 0xDEADBEEF})
    assert policy.indices() == frozenset({0, 7})
    assert policy[7] == 0xDEADBEEF


def test_policy_from_mapping_does_not_mutate_input() -> None:
    src = {7: 0x1}
    Policy.from_mapping(src)
    assert src == {7: 0x1}  # original dict untouched


def test_policy_from_mapping_empty_is_malformed() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        Policy.from_mapping({})
    assert "no expected PCRs" in str(exc.value)

PCRS_JSON = "pcrs.json"
PCRS_TAMPERED_JSON = "pcrs-tampered.json"
POLICY_TOML = "policy.toml"


def _h(n: int) -> str:
    return format(n, "064x")


# --- parse_policy: [expected] table -----------------------------------------


def test_parse_policy_expected_table() -> None:
    policy = parse_policy({"expected": {"pcr0": "0", "pcr7": "0xdeadbeef"}})
    assert policy.indices() == frozenset({0, 7})
    assert policy[0] == 0
    assert policy[7] == 0xDEADBEEF


def test_parse_policy_top_level_keys_without_expected_table() -> None:
    # Without an [expected] table the top-level keys are the expected values.
    policy = parse_policy({"pcr0": "0", "pcr7": "0xdeadbeef"})
    assert policy.indices() == frozenset({0, 7})
    assert policy[7] == 0xDEADBEEF


def test_parse_policy_integer_keys_and_values() -> None:
    policy = parse_policy({"expected": {0: 0, 7: 0xDEADBEEF}})
    assert policy.indices() == frozenset({0, 7})
    assert policy[7] == 0xDEADBEEF


def test_parse_policy_accepts_pcr_dash_n_and_plain_n() -> None:
    # Both "pcr-7" and "pcr7" spellings resolve to the same index.
    with_dash = parse_policy({"expected": {"pcr-7": "0x1"}})
    with_plain = parse_policy({"expected": {"pcr7": "0x1"}})
    assert with_dash == with_plain == Policy({7: 1})


def test_parse_policy_hex_and_decimal_values() -> None:
    policy = parse_policy({"expected": {"pcr0": "0", "pcr7": "3735928559"}})
    assert policy[7] == 0xDEADBEEF  # decimal parses too


# --- Policy.check / verify ---------------------------------------------------


def test_check_passes_on_matching_pcrs() -> None:
    policy = parse_policy({"expected": {"pcr7": "0xdeadbeef"}})
    pcrs = parse_pcrs(json.dumps({"pcr7": _h(0xDEADBEEF)}))
    assert policy.check(pcrs) == []


def test_verify_passes_silently_on_match() -> None:
    policy = parse_policy({"expected": {"pcr7": "0xdeadbeef"}})
    pcrs = parse_pcrs(json.dumps({"pcr7": _h(0xDEADBEEF)}))
    policy.verify(pcrs)  # must not raise


def test_check_reports_value_mismatch() -> None:
    policy = parse_policy({"expected": {"pcr7": "0xdeadbeef"}})
    pcrs = parse_pcrs(json.dumps({"pcr7": _h(0xCAFEBABE)}))
    mismatches = policy.check(pcrs)
    assert mismatches == [
        Mismatch(index=7, expected=0xDEADBEEF, actual=0xCAFEBABE)
    ]


def test_check_reports_missing_pcr() -> None:
    policy = parse_policy({"expected": {"pcr7": "0xdeadbeef"}})
    pcrs = parse_pcrs(json.dumps({"pcr0": _h(0)}))  # pcr7 absent
    mismatches = policy.check(pcrs)
    assert mismatches == [Mismatch(index=7, expected=0xDEADBEEF, actual=None)]


def test_check_reports_both_value_and_missing() -> None:
    policy = parse_policy(
        {"expected": {"pcr7": "0xdeadbeef", "pcr5": "0x1"}}
    )
    pcrs = parse_pcrs(json.dumps({"pcr7": _h(0xCAFEBABE)}))  # pcr5 absent, pcr7 wrong
    mismatches = policy.check(pcrs)
    assert [m.index for m in mismatches] == [5, 7]
    assert mismatches[0].actual is None
    assert mismatches[1].actual == 0xCAFEBABE


def test_verify_raises_policy_mismatch_naming_pcr() -> None:
    policy = parse_policy({"expected": {"pcr7": "0xdeadbeef"}})
    pcrs = parse_pcrs(json.dumps({"pcr7": _h(0xCAFEBABE)}))
    with pytest.raises(PolicyMismatch) as exc:
        policy.verify(pcrs)
    message = str(exc.value)
    assert "PCR 7" in message
    assert "deadbeef" in message
    assert "cafebabe" in message


def test_verify_reports_missing_pcr_in_message() -> None:
    policy = parse_policy({"expected": {"pcr7": "0xdeadbeef"}})
    pcrs = parse_pcrs(json.dumps({"pcr0": _h(0)}))
    with pytest.raises(PolicyMismatch) as exc:
        policy.verify(pcrs)
    assert "not measured" in str(exc.value)


def test_check_is_deterministic_order() -> None:
    policy = parse_policy({"expected": {"pcr3": "0x1", "pcr0": "0x2"}})
    pcrs = parse_pcrs(json.dumps({"pcr3": "0", "pcr0": "0"}))
    # Sorted by index regardless of policy key order.
    assert [m.index for m in policy.check(pcrs)] == [0, 3]


# --- Policy container behaviour ----------------------------------------------


def test_policy_get_and_contains() -> None:
    policy = parse_policy({"expected": {"pcr7": "0x1"}})
    assert 7 in policy
    assert 8 not in policy
    assert policy.get(7) == 1
    assert policy.get(8) is None
    assert policy.get(8, 99) == 99


def test_policy_indices_len_iter() -> None:
    policy = parse_policy({"expected": {"pcr0": "0", "pcr1": "1", "pcr7": "7"}})
    assert policy.indices() == frozenset({0, 1, 7})
    assert len(policy) == 3
    assert sorted(policy) == [0, 1, 7]


def test_policy_equality_with_mapping() -> None:
    policy = parse_policy({"expected": {"pcr0": "0"}})
    assert policy == {0: 0}
    assert policy != {0: 1}
    assert policy != "not a mapping"


def test_policy_is_frozen() -> None:
    policy = parse_policy({"expected": {"pcr0": "0"}})
    with pytest.raises(AttributeError):
        policy.expected = {}  # type: ignore[misc]


# --- malformed input ---------------------------------------------------------


def test_parse_policy_bad_index_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy({"expected": {"pcrX": "0"}})
    assert "must be an index" in str(exc.value)


def test_parse_policy_bad_value_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy({"expected": {"pcr7": "nothex"}})
    assert "not hex/decimal" in str(exc.value)


def test_parse_policy_boolean_value_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy({"expected": {"pcr7": True}})
    assert "boolean" in str(exc.value)


def test_parse_policy_boolean_key_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy({"expected": {True: 1}})
    assert "not a valid index" in str(exc.value)


def test_parse_policy_empty_expected_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy({"expected": {}})
    assert "no expected PCRs" in str(exc.value)


def test_parse_policy_non_mapping_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy([1, 2, 3])
    assert "TOML mapping" in str(exc.value)


def test_parse_policy_non_expected_table_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy({"expected": "not a table"})
    assert "must be a table" in str(exc.value)


def test_parse_policy_negative_integer_key_raises() -> None:
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        parse_policy({"expected": {-1: "0"}})
    assert "index must be >= 0" in str(exc.value)


# --- load_policy ------------------------------------------------------------


def test_load_policy_from_file(repo_root):
    policy = load_policy(repo_root / POLICY_TOML)
    assert policy.indices() == frozenset({0, 7})
    assert policy[7] == 0xDEADBEEF


def test_load_policy_invalid_toml(tmp_path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[expected\n", encoding="utf-8")  # unterminated table
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        load_policy(bad)
    assert "not valid TOML" in str(exc.value)


def test_load_policy_empty(tmp_path) -> None:
    empty = tmp_path / "empty.toml"
    empty.write_text("[expected]\n", encoding="utf-8")
    with pytest.raises(PengyOSMeasuredBootError) as exc:
        load_policy(empty)
    assert "no expected PCRs" in str(exc.value)


# --- committed fixtures ------------------------------------------------------


def test_good_fixture_matches_policy(repo_root) -> None:
    policy = load_policy(repo_root / POLICY_TOML)
    pcrs = parse_pcrs_file(repo_root / PCRS_JSON)
    policy.verify(pcrs)  # must not raise


def test_tampered_fixture_fails_policy(repo_root) -> None:
    policy = load_policy(repo_root / POLICY_TOML)
    pcrs = parse_pcrs_file(repo_root / PCRS_TAMPERED_JSON)
    with pytest.raises(PolicyMismatch) as exc:
        policy.verify(pcrs)
    assert "PCR 7" in str(exc.value)


def test_mismatch_dataclass_fields() -> None:
    m = Mismatch(index=7, expected=1, actual=2)
    assert m.index == 7
    assert m.expected == 1
    assert m.actual == 2


def test_policymismatch_is_domain_error() -> None:
    from pengyos_measured_boot.errors import PengyOSMeasuredBootError

    assert issubclass(PolicyMismatch, PengyOSMeasuredBootError)


def test_policymismatch_exposes_mismatches() -> None:
    policy = parse_policy({"expected": {"pcr7": "0xdeadbeef"}})
    pcrs = parse_pcrs(json.dumps({"pcr7": _h(0xCAFEBABE)}))
    with pytest.raises(PolicyMismatch) as exc:
        policy.verify(pcrs)
    assert exc.value.mismatches == [Mismatch(7, 0xDEADBEEF, 0xCAFEBABE)]
