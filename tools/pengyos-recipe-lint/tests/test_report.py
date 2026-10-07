"""Tests for the report layer (``pengyos_recipe_lint.report``).

This layer owns *output*: it turns the structured ``RuleResult`` rows from the
rule engine into ``file:line`` violations, orders them stably, renders them as
text or JSON, and derives the process exit code.
"""

from __future__ import annotations

from pathlib import Path

from pengyos_recipe_lint.report import Report, Violation, lint_recipes
from pengyos_recipe_lint.recipes import Recipe, discover_recipes


def _recipe(tmp_path: Path, name: str, text: str, hash_text: str | None = None) -> Recipe:
    """Write ``name.mk`` (+ optional ``name.hash``) and return its :class:`Recipe`."""
    mk = tmp_path / f"{name}.mk"
    mk.write_text(text, encoding="utf-8")
    if hash_text is not None:
        mk.with_suffix(".hash").write_text(hash_text, encoding="utf-8")
    return Recipe(path=mk)


def _compliant_tree(tmp_path: Path) -> Path:
    """Two fully-compliant recipes (version + sha256-for-source + license + https site)."""
    root = tmp_path / "recipes"
    root.mkdir()
    (root / "a.mk").write_text(
        "A_VERSION = 1.0\nA_SITE = https://example.org/a\n"
        "A_SOURCE = a-1.0.tar.gz\nA_LICENSE = MIT\n",
        encoding="utf-8",
    )
    (root / "a.hash").write_text(
        "sha256  " + "a" * 64 + "  a-1.0.tar.gz\n", encoding="utf-8"
    )
    (root / "b.mk").write_text(
        "B_VERSION = 2.0\nB_SITE = https://example.org/b\n"
        "B_SOURCE = b-2.0.tar.gz\nB_LICENSE = MIT\n",
        encoding="utf-8",
    )
    (root / "b.hash").write_text(
        "sha256  " + "b" * 64 + "  b-2.0.tar.gz\n", encoding="utf-8"
    )
    return root


def _violating_tree(tmp_path: Path) -> Path:
    """A tree whose recipes break rules in deterministic ways.

    * ``a.mk`` — version pinned and https site, but no ``.hash`` and no
      ``_LICENSE``.
    * ``sub/b.mk`` — no ``_VERSION``, no ``.hash``, no ``_LICENSE`` (but an
      https ``_SITE``).
    """
    root = tmp_path / "recipes"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (root / "a.mk").write_text(
        "A_VERSION = 1.0\nA_SITE = https://example.org/a\n", encoding="utf-8"
    )
    (sub / "b.mk").write_text("B_SITE = https://x\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# Violation
# --------------------------------------------------------------------------- #
def test_violation_location_with_line():
    violation = Violation("foo", "license_declared", "/x/foo.mk", 5, "msg", "hint")
    assert violation.location == "/x/foo.mk:5"


def test_violation_location_without_line():
    # A rule that cannot point at a line (e.g. a missing variable) renders just
    # the file path.
    violation = Violation("foo", "hash_present", "/x/foo.mk", None, "msg", None)
    assert violation.location == "/x/foo.mk"


def test_violation_to_dict():
    violation = Violation("foo", "license_declared", "/x/foo.mk", 5, "msg", "hint")
    assert violation.to_dict() == {
        "recipe": "foo",
        "rule": "license_declared",
        "file": "/x/foo.mk",
        "line": 5,
        "message": "msg",
        "hint": "hint",
    }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def test_report_exit_code_clean():
    report = Report(recipes=2, violations=[])
    assert report.exit_code == 0
    assert report.violation_count == 0


def test_report_exit_code_with_violations():
    report = Report(
        recipes=2,
        violations=[
            Violation("foo", "license_declared", "/x/foo.mk", 5, "msg", "hint"),
        ],
    )
    assert report.exit_code == 1
    assert report.violation_count == 1


def test_report_render_text_clean():
    report = Report(recipes=2, violations=[])
    assert report.render_text() == "OK: 2 recipe(s), 0 violations\n"


def test_report_render_text_lists_location_rule_and_hint():
    report = Report(
        recipes=1,
        violations=[
            Violation(
                "foo",
                "license_declared",
                "/x/foo.mk",
                5,
                "foo: _LICENSE is not declared",
                "Declare the license.",
            ),
        ],
    )
    text = report.render_text()
    assert "/x/foo.mk:5" in text
    assert "license_declared" in text
    assert "foo: _LICENSE is not declared" in text
    assert "hint: Declare the license." in text


def test_report_to_dict_shape():
    report = Report(
        recipes=1,
        violations=[
            Violation("foo", "license_declared", "/x/foo.mk", 5, "msg", "hint"),
        ],
    )
    assert report.to_dict() == {
        "violation_count": 1,
        "violations": [
            {
                "recipe": "foo",
                "rule": "license_declared",
                "file": "/x/foo.mk",
                "line": 5,
                "message": "msg",
                "hint": "hint",
            }
        ],
    }


# --------------------------------------------------------------------------- #
# lint_recipes
# --------------------------------------------------------------------------- #
def test_lint_recipes_clean_returns_no_violations(tmp_path):
    recipes = discover_recipes(_compliant_tree(tmp_path))
    report = lint_recipes(recipes)
    assert report.recipes == 2
    assert report.violation_count == 0
    assert report.exit_code == 0


def test_lint_recipes_flags_all_violations(tmp_path):
    recipes = discover_recipes(_violating_tree(tmp_path))
    report = lint_recipes(recipes)
    # a.mk: hash_present + license_declared (version is pinned).
    # sub/b.mk: version_pinned + hash_present + license_declared.
    assert report.violation_count == 5


def test_lint_recipes_orders_by_path_then_rule_order(tmp_path):
    recipes = discover_recipes(_violating_tree(tmp_path))
    report = lint_recipes(recipes)
    # Ordered by recipe path: a.mk (root) before sub/b.mk.
    files = [violation.file for violation in report.violations]
    assert files == sorted(files)
    # Within a.mk: hash_present before license_declared (RULE_ORDER).
    a_violations = [v for v in report.violations if v.recipe == "a"]
    assert [v.rule for v in a_violations] == ["hash_present", "license_declared"]
    # Within sub/b.mk: version_pinned before hash_present before license_declared.
    b_violations = [v for v in report.violations if v.recipe == "b"]
    assert [v.rule for v in b_violations] == [
        "version_pinned",
        "hash_present",
        "license_declared",
    ]


def test_lint_recipes_is_deterministic(tmp_path):
    recipes = discover_recipes(_violating_tree(tmp_path))
    first = lint_recipes(recipes)
    second = lint_recipes(recipes)
    assert [v.to_dict() for v in first.violations] == [
        v.to_dict() for v in second.violations
    ]
