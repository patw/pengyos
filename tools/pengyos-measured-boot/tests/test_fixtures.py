"""Tests for the committed boot fixtures.

The fixtures are first-class artifacts at the repo root:

* ``pcrs.json`` — the *good* set of PCRs, which matches ``policy.toml``.
* ``pcrs-tampered.json`` — the same set with exactly one PCR flipped, used to
  exercise the failure path.

These tests validate the fixtures' contract directly (no real ``swtpm`` is
needed): both parse, the good one matches the policy, the tampered one fails,
and the tampered one differs from the good one in *exactly* the PCR the policy
names.

The minimal-tampering property is important: a fixture used to demonstrate a
specific PCR failure should differ from the good fixture in only that PCR, so a
regression in the fixture cannot mask or widen the failure it is meant to show.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_measured_boot.errors import PolicyMismatch
from pengyos_measured_boot.pcrs import Pcrs, parse_pcrs, parse_pcrs_file
from pengyos_measured_boot.policy import Policy, load_policy

PCRS_JSON = "pcrs.json"
PCRS_TAMPERED_JSON = "pcrs-tampered.json"
POLICY_TOML = "policy.toml"


def _good(repo_root: Path) -> Pcrs:
    return parse_pcrs_file(repo_root / PCRS_JSON)


def _tampered(repo_root: Path) -> Pcrs:
    return parse_pcrs_file(repo_root / PCRS_TAMPERED_JSON)


def _policy(repo_root: Path) -> Policy:
    return load_policy(repo_root / POLICY_TOML)


# --- both fixtures are well-formed ------------------------------------------


def test_good_fixture_is_valid_json_and_parses(repo_root: Path) -> None:
    pcrs = _good(repo_root)
    assert isinstance(pcrs, Pcrs)
    assert len(pcrs) >= 1
    # Every committed digest is a 64-char sha256 (256-bit) value.
    for value in pcrs.values.values():
        assert 0 <= value < (1 << 256)


def test_tampered_fixture_is_valid_json_and_parses(repo_root: Path) -> None:
    pcrs = _tampered(repo_root)
    assert isinstance(pcrs, Pcrs)
    assert len(pcrs) >= 1


# --- good matches, tampered fails -------------------------------------------


def test_good_fixture_matches_policy(repo_root: Path) -> None:
    _policy(repo_root).verify(_good(repo_root))  # must not raise


def test_tampered_fixture_fails_policy_naming_the_pcr(repo_root: Path) -> None:
    with pytest.raises(PolicyMismatch) as exc:
        _policy(repo_root).verify(_tampered(repo_root))
    # The failure names the PCR it could not match.
    assert "PCR 7" in str(exc.value)


def test_tampered_fixture_fails_on_every_mismatching_index(repo_root: Path) -> None:
    mismatches = _policy(repo_root).check(_tampered(repo_root))
    assert [m.index for m in mismatches]  # at least one mismatching index


# --- minimal tampering: differ in exactly one PCR ---------------------------


def test_tampered_differs_from_good_in_exactly_one_pcr(repo_root: Path) -> None:
    good = _good(repo_root)
    tampered = _tampered(repo_root)

    differing = {
        index
        for index in good.indices() | tampered.indices()
        if good.get(index) != tampered.get(index)
    }
    assert len(differing) == 1

    (only_index,) = differing
    # The single differing PCR is exactly the one the policy names.
    assert only_index in {m.index for m in _policy(repo_root).check(tampered)}


def test_tampered_matches_good_everywhere_else(repo_root: Path) -> None:
    good = _good(repo_root)
    tampered = _tampered(repo_root)
    differing = {
        index
        for index in good.indices() | tampered.indices()
        if good.get(index) != tampered.get(index)
    }
    assert len(differing) == 1
    (only_index,) = differing

    for index in good.indices() | tampered.indices():
        if index == only_index:
            continue
        assert good.get(index) == tampered.get(index)


# --- the good fixture is stable (a known-good anchor) -----------------------


def test_good_fixture_pcr7_is_the_policy_value(repo_root: Path) -> None:
    policy = _policy(repo_root)
    if 7 in policy:
        assert _good(repo_root)[7] == policy[7]


def test_tampered_fixture_pcr7_is_not_the_policy_value(repo_root: Path) -> None:
    policy = _policy(repo_root)
    if 7 in policy:
        assert _tampered(repo_root)[7] != policy[7]


# --- committed fixture format round-trips through Pcrs.to_json --------------


def test_good_fixture_round_trips_through_to_json(repo_root: Path) -> None:
    original = _good(repo_root)
    round_tripped = parse_pcrs(original.to_json())
    assert round_tripped == original
