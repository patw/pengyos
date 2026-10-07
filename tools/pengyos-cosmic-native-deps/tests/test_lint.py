"""Tests for ``pengyos_cosmic_native_deps.lint`` (the recipe linter).

The linter validates the ``BR2_EXTERNAL`` recipes :mod:`.emit` writes: every
buildable recipe must carry a valid sha256 ``.hash``, pin a concrete
``<PKG>_VERSION`` (never ``HEAD`` / a branch / a URL), declare a
``<PKG>_LICENSE``, and end with a valid ``$(eval $(...))`` build-system line.
Data-only (``none``) recipes (the fonts) only need a ``Config.in``.

These tests pin the four checks in isolation, the ``.mk`` / ``.hash`` parsers,
the ``lint_dir`` / ``lint_recipe`` / ``lint_catalogue`` runners, the report
shape, the CLI contract (exit codes + JSON), and a golden lint of the emitted
catalogue — which is the "reuse it in tests" half of the item.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import pengyos_cosmic_native_deps.cli as cli
import pengyos_cosmic_native_deps.lint as lint
from pengyos_cosmic_native_deps.lint import (
    CheckName,
    Issue,
    LintReport,
    LintResult,
    LintStatus,
    RecipeKind,
    lint_catalogue,
    lint_dir,
    lint_recipe,
    parse_hash_lines,
    parse_mk,
)
from pengyos_cosmic_native_deps.recipes import default_recipe_catalogue

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- a well-formed buildable recipe body ------------------------------------

GOOD_MK = (
    "################################################################################\n"
    "# good\n#\n"
    "################################################################################\n"
    "\n"
    "GOOD_VERSION = 1.2.3\n"
    "GOOD_SOURCE = $(GOOD_VERSION).tar.gz\n"
    "GOOD_SITE = https://example.com/good\n"
    "GOOD_LICENSE = Apache-2.0\n"
    "GOOD_LICENSE_FILES = LICENSE\n"
    "\n"
    "$(eval $(meson-package))\n"
)
GOOD_HASH = "sha256  " + "a" * 64 + "  1.2.3.tar.gz\n"


def _write_recipe(
    root: pathlib.Path,
    name: str,
    *,
    mk: str | None = GOOD_MK,
    hash: str | None = GOOD_HASH,
    config_in: str | None = "config BR2_PACKAGE_GOOD\n\tbool \"good\"\n",
) -> pathlib.Path:
    """Create a ``package/<name>/`` recipe dir with the given file bodies."""
    pkg_dir = root / name
    pkg_dir.mkdir(parents=True, exist_ok=True)
    if mk is not None:
        (pkg_dir / f"{name}.mk").write_text(mk, encoding="utf-8")
    if hash is not None:
        (pkg_dir / f"{name}.hash").write_text(hash, encoding="utf-8")
    if config_in is not None:
        (pkg_dir / "Config.in").write_text(config_in, encoding="utf-8")
    return pkg_dir


# --- parse_mk ---------------------------------------------------------------


def test_parse_mk_reads_the_variable_assignments():
    values = parse_mk(GOOD_MK)
    assert values["GOOD_VERSION"] == "1.2.3"
    assert values["GOOD_SITE"] == "https://example.com/good"
    assert values["GOOD_LICENSE"] == "Apache-2.0"
    assert values["GOOD_LICENSE_FILES"] == "LICENSE"


def test_parse_mk_ignores_comments_and_the_eval_line():
    values = parse_mk(GOOD_MK)
    # The 80-hash banner and the `# good` line are comments, not variables.
    assert "#" not in values
    # The `$(eval $(...))` line is not a variable assignment.
    assert "eval" not in values and "$" not in values


def test_parse_mk_last_definition_wins():
    text = "FOO_VERSION = 1\nFOO_VERSION = 2\n"
    assert parse_mk(text)["FOO_VERSION"] == "2"


def test_parse_mk_empty_for_no_assignments():
    assert parse_mk("# only a comment\n\n") == {}


# --- parse_hash_lines -------------------------------------------------------


def test_parse_hash_lines_reads_the_three_fields():
    lines = parse_hash_lines(GOOD_HASH)
    assert lines == [("sha256", "a" * 64, "1.2.3.tar.gz")]


def test_parse_hash_lines_skips_comments_and_blanks():
    text = (
        "# a comment\n"
        "\n"
        "sha256  " + "b" * 64 + "  a.tar.gz\n"
        "  \n"
        "# trailing comment\n"
    )
    lines = parse_hash_lines(text)
    assert len(lines) == 1
    assert lines[0][0] == "sha256"


# --- _is_pinned_version -----------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["0.10.3", "1.22.1", "epoch-1.8.0", "1.0.0~rc1", "2024.02"],
)
def test_pinned_version_accepts_concrete_releases(value: str) -> None:
    assert lint._is_pinned_version(value)


@pytest.mark.parametrize("value", ["", "HEAD", "head", "https://x/y.tar.gz", "main branch"])
def test_pinned_version_rejects_unpinned_values(value: str) -> None:
    assert not lint._is_pinned_version(value)


# --- the four checks (happy path) -------------------------------------------


def test_hash_present_passes(tmp_path):
    pkg_dir = _write_recipe(tmp_path, "good", hash=GOOD_HASH)
    # A valid sha256 line must not raise.
    lint._check_hash_present(pkg_dir)


def test_version_pinned_passes():
    lint._check_version_pinned(parse_mk(GOOD_MK), "GOOD")


def test_license_declared_passes():
    lint._check_license_declared(parse_mk(GOOD_MK), "GOOD")


def test_correct_infra_passes():
    lint._check_correct_infra(GOOD_MK)


# --- the four checks (each violation) ---------------------------------------


def test_hash_present_missing_file(tmp_path):
    pkg_dir = _write_recipe(tmp_path, "nohash", hash=None)
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_hash_present(pkg_dir)
    assert exc.value.check is CheckName.HASH_PRESENT
    assert "hash" in exc.value.message.lower()


def test_hash_present_non_sha256_type(tmp_path):
    bad = "sha1  " + "a" * 40 + "  1.2.3.tar.gz\n"
    pkg_dir = _write_recipe(tmp_path, "sha1", hash=bad)
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_hash_present(pkg_dir)
    assert exc.value.check is CheckName.HASH_PRESENT
    assert "sha256" in exc.value.message


def test_hash_present_bad_digest_length(tmp_path):
    bad = "sha256  " + "a" * 40 + "  1.2.3.tar.gz\n"  # 40, not 64
    pkg_dir = _write_recipe(tmp_path, "short", hash=bad)
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_hash_present(pkg_dir)
    assert exc.value.check is CheckName.HASH_PRESENT
    assert "64-hex" in exc.value.message


def test_hash_present_no_lines(tmp_path):
    pkg_dir = _write_recipe(tmp_path, "empty", hash="# only a comment\n")
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_hash_present(pkg_dir)
    assert exc.value.check is CheckName.HASH_PRESENT
    assert "no hash" in exc.value.message


def test_version_pinned_missing_variable():
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_version_pinned({}, "GOOD")
    assert exc.value.check is CheckName.VERSION_PINNED
    assert "VERSION" in exc.value.message


def test_version_pinned_head():
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_version_pinned({"GOOD_VERSION": "HEAD"}, "GOOD")
    assert exc.value.check is CheckName.VERSION_PINNED
    assert "HEAD" in exc.value.message


def test_version_pinned_url():
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_version_pinned({"GOOD_VERSION": "https://x/y"}, "GOOD")
    assert exc.value.check is CheckName.VERSION_PINNED


def test_license_declared_missing():
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_license_declared({}, "GOOD")
    assert exc.value.check is CheckName.LICENSE_DECLARED


def test_license_declared_empty():
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_license_declared({"GOOD_LICENSE": ""}, "GOOD")
    assert exc.value.check is CheckName.LICENSE_DECLARED
    assert "empty" in exc.value.message


def test_correct_infra_missing_line():
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_correct_infra(GOOD_MK.replace("$(eval $(meson-package))", ""))
    assert exc.value.check is CheckName.CORRECT_INFRA
    assert "eval" in exc.value.message.lower()


def test_correct_infra_unknown_method():
    bad_mk = GOOD_MK.replace("meson-package", "foo-package")
    with pytest.raises(lint.LintIssue) as exc:
        lint._check_correct_infra(bad_mk)
    assert exc.value.check is CheckName.CORRECT_INFRA
    assert "foo-package" in exc.value.message


@pytest.mark.parametrize(
    "method",
    ["meson-package", "autotools-package", "generic-package", "cargo-package"],
)
def test_correct_infra_accepts_every_known_method(method: str) -> None:
    text = GOOD_MK.replace("meson-package", method)
    lint._check_correct_infra(text)  # must not raise


# --- lint_recipe ------------------------------------------------------------


def test_lint_recipe_buildable_ok(tmp_path):
    pkg_dir = _write_recipe(tmp_path, "good")
    result = lint_recipe(pkg_dir)
    assert result.kind is RecipeKind.BUILDABLE
    assert result.status is LintStatus.OK
    assert result.ok
    assert result.issues == ()


def test_lint_recipe_buildable_collects_every_violation(tmp_path):
    # Missing hash + HEAD version + empty license + bad infra — all four.
    pkg_dir = _write_recipe(
        tmp_path,
        "bad",
        mk=(
            "BAD_VERSION = HEAD\n"
            "BAD_SITE = https://example.com\n"
            "BAD_LICENSE =\n"
            "$(eval $(foo-package))\n"
        ),
        hash=None,
    )
    result = lint_recipe(pkg_dir)
    assert result.status is LintStatus.VIOLATION
    checks = {issue.check for issue in result.issues}
    assert checks == {
        CheckName.HASH_PRESENT,
        CheckName.VERSION_PINNED,
        CheckName.LICENSE_DECLARED,
        CheckName.CORRECT_INFRA,
    }


def test_lint_recipe_data_only_needs_config_in(tmp_path):
    pkg_dir = _write_recipe(tmp_path, "fonts", mk=None, hash=None)
    result = lint_recipe(pkg_dir)
    assert result.kind is RecipeKind.DATA_ONLY
    assert result.status is LintStatus.OK


def test_lint_recipe_data_only_empty_dir_violates(tmp_path):
    pkg_dir = tmp_path / "_t_empty"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    result = lint_recipe(pkg_dir)
    assert result.kind is RecipeKind.DATA_ONLY
    assert result.status is LintStatus.VIOLATION
    assert result.issues[0].check is CheckName.HASH_PRESENT


# --- lint_dir ---------------------------------------------------------------


def test_lint_dir_reports_ok_for_a_clean_tree(tmp_path):
    _write_recipe(tmp_path, "good")
    _write_recipe(tmp_path, "other", mk=GOOD_MK.replace("GOOD", "OTHER"),
                  hash=GOOD_HASH)
    report = lint_dir(tmp_path)
    assert report.ok
    assert [r.name for r in report.results] == ["good", "other"]
    assert all(r.status is LintStatus.OK for r in report.results)


def test_lint_dir_reports_violations_and_ok_split(tmp_path):
    _write_recipe(tmp_path, "good")
    _write_recipe(
        tmp_path,
        "bad",
        mk="BAD_VERSION = HEAD\nBAD_SITE = https://x\nBAD_LICENSE = MIT\n$(eval $(cargo-package))\n",
        hash=None,
    )
    report = lint_dir(tmp_path)
    assert not report.ok
    assert [r.name for r in report.violations] == ["bad"]
    ok_names = {r.name for r in report.results if r.status is LintStatus.OK}
    assert ok_names == {"good"}


def test_lint_dir_empty_directory_is_ok(tmp_path):
    report = lint_dir(tmp_path / "_does_not_exist")
    assert report.ok
    assert report.results == ()


def test_lint_dir_skips_hidden_directories(tmp_path):
    _write_recipe(tmp_path, "good")
    (tmp_path / ".hidden").mkdir()
    report = lint_dir(tmp_path)
    assert [r.name for r in report.results] == ["good"]


# --- lint_catalogue (golden) ------------------------------------------------


def test_lint_catalogue_is_clean_for_the_committed_catalogue(tmp_path):
    """The catalogue :mod:`.emit` writes must lint clean — the reuse hook."""
    report = lint_catalogue(default_recipe_catalogue(), tmp_path / "tree")
    assert report.ok, [i for r in report.results for i in r.issues]
    # All four authored recipes are present.
    assert {r.name for r in report.results} == {
        "greetd",
        "xdg-desktop-portal",
        "cosmic-portal",
        "fonts",
    }


def test_lint_catalogue_marks_the_fonts_data_only(tmp_path):
    report = lint_catalogue(default_recipe_catalogue(), tmp_path / "tree")
    fonts = next(r for r in report.results if r.name == "fonts")
    assert fonts.kind is RecipeKind.DATA_ONLY
    greetd = next(r for r in report.results if r.name == "greetd")
    assert greetd.kind is RecipeKind.BUILDABLE


# --- the report API ---------------------------------------------------------


def test_lint_report_as_dict_shape():
    report = LintReport(
        root=ROOT,
        results=(
            LintResult("good", RecipeKind.BUILDABLE, LintStatus.OK),
            LintResult(
                "bad",
                RecipeKind.BUILDABLE,
                LintStatus.VIOLATION,
                (Issue(CheckName.HASH_PRESENT, "no hash"),),
            ),
        ),
    )
    data = report.as_dict()
    assert data["ok"] is False
    assert data["counts"] == {"recipes": 2, "violations": 1}
    assert len(data["results"]) == 2
    bad = data["results"][1]
    assert bad["status"] == "violation"
    assert bad["issues"][0]["check"] == "hash_present"
    # Deterministic: a re-serialisation is byte-identical.
    assert json.loads(json.dumps(data)) == data


def test_lint_report_ok_when_all_clean():
    report = LintReport(
        root=ROOT,
        results=(
            LintResult("a", RecipeKind.BUILDABLE, LintStatus.OK),
            LintResult("b", RecipeKind.DATA_ONLY, LintStatus.OK),
        ),
    )
    assert report.ok


# --- the CLI ----------------------------------------------------------------


def _emit_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """Emit the committed catalogue into a fresh BR2_EXTERNAL tree."""
    from pengyos_cosmic_native_deps.emit import emit

    emit(default_recipe_catalogue(), tmp_path)
    return tmp_path


def test_lint_handler_exits_zero_for_a_clean_tree(tmp_path):
    tree = _emit_tree(tmp_path)
    rc = cli.main(["lint", "--dir", str(tree / "package")])
    assert rc == 0


def test_lint_handler_exits_nonzero_for_a_violating_tree(tmp_path):
    tree = _emit_tree(tmp_path)
    # Corrupt one recipe: strip its hash and pin HEAD.
    bad = tree / "package" / "greetd"
    (bad / "greetd.hash").unlink()
    mk = (bad / "greetd.mk").read_text(encoding="utf-8").replace(
        "GREETD_VERSION = 0.10.3", "GREETD_VERSION = HEAD"
    )
    (bad / "greetd.mk").write_text(mk, encoding="utf-8")
    rc = cli.main(["lint", "--dir", str(tree / "package")])
    assert rc == 1


def test_lint_handler_text_table_lists_every_recipe(tmp_path, capsys):
    tree = _emit_tree(tmp_path)
    rc = cli.main(["lint", "--dir", str(tree / "package")])
    assert rc == 0
    out = capsys.readouterr().out
    for name in ("greetd", "xdg-desktop-portal", "cosmic-portal", "fonts"):
        assert name in out
    assert "0 violation(s)" in out


def test_lint_handler_json_report(tmp_path, capsys):
    tree = _emit_tree(tmp_path)
    rc = cli.main(["lint", "--dir", str(tree / "package"), "--json", str(tmp_path / "r.json")])
    assert rc == 0
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["counts"]["violations"] == 0
    assert {r["name"] for r in report["results"]} == {
        "greetd",
        "xdg-desktop-portal",
        "cosmic-portal",
        "fonts",
    }


def test_lint_handler_json_report_is_deterministic(tmp_path):
    tree = _emit_tree(tmp_path)
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    cli.main(["lint", "--dir", str(tree / "package"), "--json", str(a)])
    cli.main(["lint", "--dir", str(tree / "package"), "--json", str(b)])
    assert a.read_bytes() == b.read_bytes()
