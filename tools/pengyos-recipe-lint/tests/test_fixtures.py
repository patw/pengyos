"""Tests for the committed good/bad recipe fixtures.

These lock in the contract that:

* every recipe under ``fixtures/good`` passes **all** three rules, and
* every recipe under ``fixtures/bad`` fails **exactly** the rule(s) it was
  written to demonstrate (and passes the rest).

They also assert that the on-disk fixture files and the ``EXPECTED_FAILURES``
table stay in sync, and that the bad fixtures collectively exercise every rule.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pengyos_recipe_lint.recipe_parser import RecipeSource
from pengyos_recipe_lint.rules import RULE_ORDER, RuleId, check_recipe
from pengyos_recipe_lint.recipes import discover_recipes

# One level above tests/ -> the committed fixtures/ tree.
FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
GOOD_FIXTURES = FIXTURES_DIR / "good"
BAD_FIXTURES = FIXTURES_DIR / "bad"


# Each bad recipe is expected to fail exactly these rules (and pass the rest).
EXPECTED_FAILURES: dict[str, frozenset[RuleId]] = {
    "unpinned_version": frozenset({RuleId.VERSION_PINNED}),
    "missing_hash": frozenset({RuleId.HASH_PRESENT}),
    "missing_license": frozenset({RuleId.LICENSE_DECLARED}),
    "wrong_source_hash": frozenset({RuleId.HASH_PRESENT}),
    "unverified_site": frozenset({RuleId.NO_UNVERIFIED_DOWNLOADS}),
    "broken_all": frozenset(
        {
            RuleId.VERSION_PINNED,
            RuleId.HASH_PRESENT,
            RuleId.LICENSE_DECLARED,
        }
    ),
}


def _failed(results) -> set[RuleId]:
    return {r.rule for r in results if not r.ok}


def _passed(results) -> set[RuleId]:
    return {r.rule for r in results if r.ok}


# --------------------------------------------------------------------------- #
# good fixtures: every rule passes.
# --------------------------------------------------------------------------- #
def test_good_fixtures_exist_and_are_non_empty(good_fixtures):
    assert GOOD_FIXTURES.is_dir()
    assert list(GOOD_FIXTURES.glob("*.mk")), "fixtures/good should contain recipes"


def test_good_fixtures_all_rules_pass(good_fixtures):
    recipes = discover_recipes(good_fixtures)
    assert recipes, "fixtures/good should not be empty"

    for recipe in recipes:
        source = RecipeSource.from_file(recipe.path)
        results = check_recipe(recipe, source)
        # Every implemented rule ran, in the canonical order.
        assert [r.rule for r in results] == list(RULE_ORDER)
        # ... and every one passed.
        assert _failed(results) == set(), (
            f"{recipe.name} passed unexpectedly:\n"
            + "\n".join(f"  - {r.rule}: {r.message}" for r in results if not r.ok)
        )


# --------------------------------------------------------------------------- #
# bad fixtures: each fails exactly its expected rule(s).
# --------------------------------------------------------------------------- #
def test_bad_fixtures_exist_and_are_non_empty(bad_fixtures):
    assert BAD_FIXTURES.is_dir()
    assert list(BAD_FIXTURES.glob("*.mk")), "fixtures/bad should contain recipes"


def test_bad_fixture_files_match_expected_table(bad_fixtures):
    # The set of .mk files on disk must equal the keys of EXPECTED_FAILURES,
    # so no fixture is ever left unverified (and no stale table entry remains).
    recipes = {r.name for r in discover_recipes(bad_fixtures)}
    assert recipes == set(EXPECTED_FAILURES), (
        f"fixture files and EXPECTED_FAILURES disagree on: {recipes ^ set(EXPECTED_FAILURES)}"
    )


def test_bad_fixtures_fail_expected_rules(bad_fixtures):
    recipes = {r.name: r for r in discover_recipes(bad_fixtures)}

    for name, recipe in recipes.items():
        source = RecipeSource.from_file(recipe.path)
        results = check_recipe(recipe, source)
        failed = _failed(results)
        expected = EXPECTED_FAILURES[name]
        assert failed == expected, (
            f"{name} failed {sorted(r.value for r in failed)}; "
            f"expected {sorted(r.value for r in expected)}"
        )


def test_bad_fixtures_cover_every_rule():
    # The bad fixtures collectively exercise each of the three rules, so the
    # fixture set is a meaningful regression net rather than a narrow one.
    covered: set[RuleId] = set()
    for failing in EXPECTED_FAILURES.values():
        covered |= set(failing)
    assert covered == set(RULE_ORDER)


# --------------------------------------------------------------------------- #
# End-to-end CLI on the committed fixtures (opt-in integration test).
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="opt-in integration test (set PENGYOS_IT=1)",
)
def test_cli_check_good_fixtures_exits_zero(run_cli, good_fixtures, capsys):
    rc = run_cli("check", str(good_fixtures))
    assert rc == 0
    assert "0 violations" in capsys.readouterr().out


@pytest.mark.skipif(
    not os.environ.get("PENGYOS_IT"),
    reason="opt-in integration test (set PENGYOS_IT=1)",
)
def test_cli_check_bad_fixtures_exits_nonzero(run_cli, bad_fixtures, capsys):
    rc = run_cli("check", str(bad_fixtures))
    assert rc == 1
    out = capsys.readouterr().out
    # Every broken fixture's name shows up in the report.
    for name in EXPECTED_FAILURES:
        assert name in out
