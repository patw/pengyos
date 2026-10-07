"""Tests for ``pengyos_cosmic_native_deps.cli``.

Covers argument parsing, dispatch, the ``list`` output, and the clean error
contract (a ``CliError`` maps to a one-line stderr message and exit 1 — never a
traceback).
"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

import pengyos_cosmic_native_deps.cli as cli


def test_build_parser_registers_all_subcommands():
    parser = cli.build_parser()
    action = next(
        a for a in parser._actions if type(a).__name__ == "_SubParsersAction"
    )
    assert sorted(action.choices) == [
        "classify",
        "emit",
        "lint",
        "list",
        "recipes",
        "verify",
    ]


def test_subcommand_required():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "pengyos-cosmic-native-deps" in capsys.readouterr().out


# --- list -------------------------------------------------------------------


def test_list_text(capsys):
    rc = cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    # Every candidate name appears in the table.
    for name in ("greetd", "xdg-desktop-portal", "cosmic-portal", "libdisplay-info", "seatd", "fonts"):
        assert name in out
    # greetd's symbol is surfaced.
    assert "BR2_PACKAGE_GREETD" in out


def test_list_json_output(capsys):
    rc = cli.main(["list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert {d["name"] for d in payload} == {
        "greetd",
        "xdg-desktop-portal",
        "cosmic-portal",
        "libdisplay-info",
        "seatd",
        "fonts",
    }
    greetd = next(d for d in payload if d["name"] == "greetd")
    assert greetd["br_symbols"] == ["BR2_PACKAGE_GREETD"]
    assert greetd["description"]
    assert greetd["status"] in ("shipped", "recipe_needed")


def test_list_text_shows_the_status_of_every_candidate(capsys):
    rc = cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    # The shipped/recipe-needed status column is surfaced for both outcomes.
    assert "recipe_needed" in out
    assert "shipped" in out
    # greetd is recipe-needed, libdisplay-info is shipped (the 2026.08 reality).
    assert "greetd" in out and "recipe_needed" in out
    assert "libdisplay-info" in out and "shipped" in out


# --- classify ---------------------------------------------------------------


def test_classify_text(capsys):
    rc = cli.main(["classify"])
    assert rc == 0
    out = capsys.readouterr().out
    # Both outcomes are surfaced, with the per-candidate reason.
    assert "recipe_needed" in out
    assert "shipped" in out
    assert "greetd" in out
    assert "libdisplay-info" in out
    # The provenance tag is printed.
    assert "2026.08" in out


def test_classify_json_matches_the_committed_data(capsys):
    rc = cli.main(["classify", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["buildroot_tag"] == "2026.08"
    by_name = {e["name"]: e for e in payload["classification"]}
    assert by_name["greetd"]["status"] == "recipe_needed"
    assert by_name["libdisplay-info"]["status"] == "shipped"
    assert by_name["fonts"]["status"] == "recipe_needed"
    # Every entry carries a human reason.
    assert all(e["reason"] for e in payload["classification"])


def test_classify_matches_the_committed_data_file(capsys):
    """The CLI's `classify --json` equals the committed data file."""
    import json
    import pathlib

    cli.main(["classify", "--json"])
    payload = json.loads(capsys.readouterr().out)
    data_file = pathlib.Path(__file__).resolve().parent.parent / "data" / "classification.json"
    committed = json.loads(data_file.read_text(encoding="utf-8"))
    assert committed == payload



# --- recipes ----------------------------------------------------------------


def test_recipes_text(capsys):
    rc = cli.main(["recipes"])
    assert rc == 0
    out = capsys.readouterr().out
    # Every authored recipe name appears in the table.
    for name in ("greetd", "xdg-desktop-portal", "cosmic-portal", "fonts"):
        assert name in out
    # The pinned versions and build systems are surfaced.
    assert "0.10.3" in out and "1.22.1" in out
    assert "cargo" in out and "meson" in out and "none" in out


def test_recipes_json_matches_the_committed_data(capsys):
    rc = cli.main(["recipes", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [d["name"] for d in payload] == [
        "greetd",
        "xdg-desktop-portal",
        "cosmic-portal",
        "fonts",
    ]
    greetd = next(d for d in payload if d["name"] == "greetd")
    assert greetd["version"] == "0.10.3"
    assert greetd["build_system"] == "cargo"
    assert greetd["hash"]
    # The data-only fonts recipe carries no build fields.
    fonts = next(d for d in payload if d["name"] == "fonts")
    assert fonts["build_system"] == "none"


def test_recipes_json_equals_the_committed_data_file(capsys):
    """The CLI's `recipes --json` equals the committed data/recipes.json."""
    cli.main(["recipes", "--json"])
    payload = json.loads(capsys.readouterr().out)
    committed = json.loads(
        (
            pathlib.Path(__file__).resolve().parent.parent / "data" / "recipes.json"
        ).read_text(encoding="utf-8")
    )
    assert payload == committed


def test_emit_handler_writes_recipes_and_fragment(tmp_path):
    """emit writes the recipes + fragment into --out and returns 0."""
    rc = cli.main(["emit", "--out", str(tmp_path)])
    assert rc == 0
    out = pathlib.Path(tmp_path)
    # Every buildable recipe yields the Config.in/.mk/.hash trio.
    for name in ("greetd", "xdg-desktop-portal", "cosmic-portal"):
        assert (out / "package" / name / f"{name}.mk").is_file()
        assert (out / "package" / name / f"{name}.hash").is_file()
    # The data-only (fonts) recipe yields only Config.in — no .mk/.hash.
    assert (out / "package" / "fonts" / "Config.in").is_file()
    assert not (out / "package" / "fonts" / "fonts.mk").exists()
    # The enabling fragment is written.
    assert (out / "fragments" / "cosmic-native.fragment").is_file()


def test_emit_json_report(tmp_path, capsys):
    """emit --json prints the deterministic report (11 files) and exits 0."""
    rc = cli.main(["emit", "--out", str(tmp_path), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["count"] == 11
    paths = sorted(f["path"] for f in report["files"])
    assert paths == [
        "fragments/cosmic-native.fragment",
        "package/cosmic-portal/Config.in",
        "package/cosmic-portal/cosmic-portal.hash",
        "package/cosmic-portal/cosmic-portal.mk",
        "package/fonts/Config.in",
        "package/greetd/Config.in",
        "package/greetd/greetd.hash",
        "package/greetd/greetd.mk",
        "package/xdg-desktop-portal/Config.in",
        "package/xdg-desktop-portal/xdg-desktop-portal.hash",
        "package/xdg-desktop-portal/xdg-desktop-portal.mk",
    ]


def test_lint_handler_exits_zero_for_a_clean_tree(tmp_path):
    """lint exits 0 when every recipe is lint-clean (the real behavior)."""
    from pengyos_cosmic_native_deps.emit import emit
    from pengyos_cosmic_native_deps.recipes import default_recipe_catalogue

    emit(default_recipe_catalogue(), tmp_path)
    rc = cli.main(["lint", "--dir", str(tmp_path / "package")])
    assert rc == 0


def test_lint_handler_exits_nonzero_for_a_violating_tree(tmp_path, capsys):
    """lint exits 1 (no traceback) when a recipe violates a check."""
    from pengyos_cosmic_native_deps.emit import emit
    from pengyos_cosmic_native_deps.recipes import default_recipe_catalogue

    emit(default_recipe_catalogue(), tmp_path)
    # Corrupt greetd: drop its hash and pin the version to HEAD.
    greetd = tmp_path / "package" / "greetd"
    (greetd / "greetd.hash").unlink()
    (greetd / "greetd.mk").write_text(
        (greetd / "greetd.mk").read_text(encoding="utf-8").replace(
            "GREETD_VERSION = 0.10.3", "GREETD_VERSION = HEAD"
        ),
        encoding="utf-8",
    )
    rc = cli.main(["lint", "--dir", str(tmp_path / "package")])
    assert rc == 1
    # A clean one-line message, never a traceback.
    err = capsys.readouterr().err
    assert "Traceback" not in err


def test_usage_error_exit_code_2():
    with pytest.raises(SystemExit) as exc:
        cli.main(["emit"])  # missing required --out
    assert exc.value.code == 2
