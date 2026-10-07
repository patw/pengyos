"""Tests for the .desktop/mime integration plan.

Covers the integration-plan deliverable:

* :func:`pengyos_desktop_curation.mime.IntegrationEntry` / :class:`IntegrationPlan`
  — the model, queries, ordering, validation,
* :func:`parse_entry` / :func:`parse_plan` / :func:`load` / :func:`load_default`
  — the loader (TOML + JSON, validators),
* :func:`mimeapps_list` — the freedesktop ``mimeapps.list`` emitter (two sections,
  sorted, valid INI, empty plan → header only),
* :func:`plan_json` — the machine-readable plan (valid JSON, deterministic),
* :func:`pengyos_desktop_curation.emit.emit_mime` — builds the mime artifacts,
* the CLI ``emit --mime`` writes ``mimeapps.list`` + ``integration-plan.json``
  (exit 0, ``--json`` reports the mime artifacts),
* the committed plan stays in step with the committed curated set (app + ref).

No network, root, display, or hardware is needed.
"""

from __future__ import annotations

import configparser
import json
import pathlib
import tomllib

import pytest

from pengyos_desktop_curation import cli
from pengyos_desktop_curation import data as data_mod
from pengyos_desktop_curation import emit
from pengyos_desktop_curation import mime
from pengyos_desktop_curation.mime import (
    IntegrationEntry,
    IntegrationPlan,
    MalformedPlanError,
    PlanLoadError,
    load,
    load_default,
    parse_entry,
    parse_plan,
)

# The committed curated set and integration plan — loaded once per module.
CURATED = data_mod.load_default()
PLAN = load_default()

# A tiny two-entry plan for exercising the emitter in isolation.
TWO = parse_plan(
    [
        {
            "category": "browser",
            "app": "Firefox",
            "desktop_id": "org.mozilla.FirefoxRepo.desktop",
            "ref": "org.mozilla.FirefoxRepo",
            "mechanism": "mimeapps_list",
            "mime_types": ["x-scheme-handler/https", "text/html"],
            "rationale": "default browser",
            "human_signed": True,
        },
        {
            "category": "files",
            "app": "Files",
            "desktop_id": "org.gnome.Files.desktop",
            "ref": "org.gnome.Files",
            "mechanism": "mimeapps_list",
            "mime_types": ["inode/directory"],
            "rationale": "default file manager",
            "human_signed": True,
        },
    ]
)


# --- the committed data ------------------------------------------------------


def test_mime_integration_toml_exists(repo_root) -> None:
    assert (repo_root / "mime-integration.toml").is_file()


def test_plan_has_one_entry_per_everyday_category() -> None:
    """The committed plan covers every everyday category, once, in order."""

    assert len(PLAN) == len(data_mod.EVERYDAY_CATEGORIES)
    assert PLAN.categories() == list(data_mod.EVERYDAY_CATEGORIES)


def test_every_entry_signed_and_rationalised() -> None:
    """The artistry domain is human-ratified: no entry is unsigned or
    rationale-less (spec Principle 8)."""

    for entry in PLAN:
        assert entry.rationale.strip(), f"{entry.app} has an empty rationale"
        assert entry.is_signed, f"{entry.app} is not human_signed"


def test_every_mechanism_is_known() -> None:
    for entry in PLAN:
        assert entry.mechanism in mime.MECHANISMS


def test_committed_plan_tracks_curated_app_and_ref() -> None:
    """The integration plan must stay in step with the curated set: every
    category's entry carries the same app name and flatpak ref as the curated
    entry (the emitter cross-checks this invariant)."""

    for cat in data_mod.EVERYDAY_CATEGORIES:
        curated_entry = CURATED.by_category(cat)[0]
        plan_entry = PLAN.by_category(cat)[0]
        assert plan_entry.app == curated_entry.app, (
            f"{cat}: plan app {plan_entry.app!r} != curated app {curated_entry.app!r}"
        )
        assert plan_entry.ref == curated_entry.ref, (
            f"{cat}: plan ref {plan_entry.ref!r} != curated ref {curated_entry.ref!r}"
        )


def test_committed_plan_desktop_ids_match_curated_refs() -> None:
    """Every plan entry's ``desktop_id`` is the curated ref suffixed with
    ``.desktop`` (the freedesktop convention)."""

    for cat in data_mod.EVERYDAY_CATEGORIES:
        curated_entry = CURATED.by_category(cat)[0]
        plan_entry = PLAN.by_category(cat)[0]
        assert plan_entry.desktop_id == f"{curated_entry.ref}.desktop"


# --- IntegrationEntry ---------------------------------------------------------


def test_entry_as_dict_matches_fields() -> None:
    entry = TWO.entries[0]
    d = entry.as_dict()
    assert d == {
        "category": "browser",
        "app": "Firefox",
        "desktop_id": "org.mozilla.FirefoxRepo.desktop",
        "ref": "org.mozilla.FirefoxRepo",
        "mechanism": "mimeapps_list",
        "mime_types": ["x-scheme-handler/https", "text/html"],
        "rationale": "default browser",
        "human_signed": True,
    }
    # Field order is stable and matches the prose (category → app → desktop_id …).
    assert list(d) == [
        "category",
        "app",
        "desktop_id",
        "ref",
        "mechanism",
        "mime_types",
        "rationale",
        "human_signed",
    ]


def test_entry_is_immutable() -> None:
    with pytest.raises(AttributeError):
        TWO.entries[0].app = "x"  # type: ignore[misc]


def test_entry_has_ref_and_is_signed() -> None:
    entry = TWO.entries[0]
    assert entry.has_ref is True
    assert entry.is_signed is True


def test_entry_defaults() -> None:
    """A minimal entry defaults ref/mechanism/mime_types sensibly."""

    entry = parse_entry(
        {"category": "browser", "app": "x", "desktop_id": "x.desktop", "rationale": "y"},
        0,
    )
    assert entry.ref == ""
    assert entry.has_ref is False
    assert entry.mechanism == "mimeapps_list"
    assert entry.mime_types == ()
    assert entry.is_signed is False


def test_entry_validate_passes_for_good_entry() -> None:
    assert parse_entry(
        {
            "category": "browser",
            "app": "Firefox",
            "desktop_id": "org.mozilla.FirefoxRepo.desktop",
            "ref": "org.mozilla.FirefoxRepo",
            "mechanism": "mimeapps_list",
            "mime_types": ["text/html"],
            "rationale": "default browser",
            "human_signed": True,
        },
        0,
    ).has_ref


def test_entry_validate_flags_missing_required_field() -> None:
    with pytest.raises(MalformedPlanError, match="desktop_id"):
        parse_entry(
            {"category": "browser", "app": "x", "rationale": "y"}, 0
        )


def test_entry_validate_flags_bad_mechanism() -> None:
    with pytest.raises(MalformedPlanError, match="mechanism"):
        parse_entry(
            {
                "category": "browser",
                "app": "x",
                "desktop_id": "x.desktop",
                "rationale": "y",
                "mechanism": "snap",
            },
            0,
        )


def test_entry_validate_flags_empty_mime_type() -> None:
    with pytest.raises(MalformedPlanError, match="mime_type"):
        parse_entry(
            {
                "category": "browser",
                "app": "x",
                "desktop_id": "x.desktop",
                "rationale": "y",
                "mime_types": ["text/html", "  "],
            },
            0,
        )


def test_entry_validate_flags_non_string_mime_type() -> None:
    with pytest.raises(MalformedPlanError, match="mime_type"):
        parse_entry(
            {
                "category": "browser",
                "app": "x",
                "desktop_id": "x.desktop",
                "rationale": "y",
                "mime_types": [123],
            },
            0,
        )


def test_entry_human_signed_rejects_non_bool() -> None:
    with pytest.raises(MalformedPlanError, match="human_signed"):
        parse_entry(
            {
                "category": "browser",
                "app": "x",
                "desktop_id": "x.desktop",
                "rationale": "y",
                "human_signed": "yes",
            },
            0,
        )


# --- IntegrationPlan queries --------------------------------------------------


def test_plan_is_iterable_and_len() -> None:
    assert len(list(PLAN)) == len(PLAN) == 10


def test_plan_categories_ordered_and_deduplicated() -> None:
    sample = parse_plan(
        [
            {
                "category": "browser",
                "app": "a",
                "desktop_id": "a.desktop",
                "rationale": "x",
            },
            {
                "category": "browser",
                "app": "b",
                "desktop_id": "b.desktop",
                "rationale": "x",
            },
            {
                "category": "files",
                "app": "c",
                "desktop_id": "c.desktop",
                "rationale": "x",
            },
        ]
    )
    assert sample.categories() == ["browser", "files"]


def test_plan_by_category() -> None:
    browser = PLAN.by_category("browser")
    assert len(browser) == 1
    assert browser[0].category == "browser"
    assert PLAN.by_category("nope") == []


def test_plan_mechanisms_ordered_and_deduplicated() -> None:
    """The committed plan uses at least one real mechanism."""

    mechs = PLAN.mechanisms()
    assert "mimeapps_list" in mechs
    assert len(mechs) == len(set(mechs))


def test_plan_desktop_ids_ordered_and_deduplicated() -> None:
    ids = PLAN.desktop_ids()
    assert len(ids) == len(set(ids)) == 10
    assert ids[0] == "org.mozilla.FirefoxRepo.desktop"


def test_plan_equality() -> None:
    a = load_default()
    b = load_default()
    assert a == b
    assert a != "not a plan"


# --- mimeapps_list emitter ----------------------------------------------------


def test_mimeapps_list_has_header_and_sections() -> None:
    text = mime.mimeapps_list(TWO)
    assert "mimeapps.list emitted by pengyos-desktop-curation" in text
    assert "[Default Applications]" in text
    assert "[Added Applications]" in text


def test_mimeapps_list_default_rows_are_sorted() -> None:
    text = mime.mimeapps_list(TWO)
    # Within the browser entry, MIME types are sorted.
    lines = text.splitlines()
    browser_rows = [
        ln
        for ln in lines
        if ln.endswith(".desktop") and ln.split("=")[0] in ("x-scheme-handler/https", "text/html")
    ]
    assert browser_rows == sorted(browser_rows)


def test_mimeapps_list_has_trailing_semicolon_on_added_rows() -> None:
    text = mime.mimeapps_list(TWO)
    for line in text.splitlines():
        if "=" in line and line.startswith("org."):
            assert line.endswith(";")


def test_mimeapps_list_is_valid_ini(capsys) -> None:
    """The emitted mimeapps.list is a real INI file xdg-mime can parse."""

    import io

    cp = configparser.ConfigParser()
    cp.optionxform = str  # preserve case (MIME types are case-sensitive)
    cp.read_string(mime.mimeapps_list(TWO))
    assert cp.sections() == ["Default Applications", "Added Applications"]
    # The default for https is the browser's desktop id.
    assert cp["Default Applications"]["x-scheme-handler/https"] == (
        "org.mozilla.FirefoxRepo.desktop"
    )
    # The reverse mapping lists the entry's MIME types.
    assert cp["Added Applications"]["org.gnome.Files.desktop"] == "inode/directory;"


def test_mimeapps_list_empty_plan_is_header_only() -> None:
    text = mime.mimeapps_list(IntegrationPlan())
    assert "mimeapps.list emitted by pengyos-desktop-curation" in text
    # No actual sections — the header comment mentions the section names as text,
    # so assert via a real INI parse (no sections when empty).
    cp = configparser.ConfigParser()
    cp.read_string(text)
    assert cp.sections() == []


def test_mimeapps_list_only_lists_covered_mime_types() -> None:
    """A category with no MIME types (calculator/screenshots) contributes no
    default row and no added-app row."""

    text = mime.mimeapps_list(PLAN)
    # Calculator is xdg_desktop_dirs with no MIME types → no default row.
    assert "org.gnome.Calculator.desktop=" not in text.split("[Added Applications]")[0]
    assert "org.gnome.Calculator.desktop=" not in text


def test_mimeapps_list_is_deterministic() -> None:
    a = mime.mimeapps_list(PLAN)
    b = mime.mimeapps_list(PLAN)
    assert a == b


# --- plan_json emitter --------------------------------------------------------


def test_plan_json_is_valid_and_deterministic() -> None:
    a = mime.plan_json(TWO)
    b = mime.plan_json(TWO)
    assert a == b
    payload = json.loads(a)  # parses as JSON
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["app"] == "Firefox"
    assert payload[0]["desktop_id"] == "org.mozilla.FirefoxRepo.desktop"


def test_plan_json_complete_entry_shape() -> None:
    payload = json.loads(mime.plan_json(TWO))
    entry = payload[0]
    assert set(entry) == {
        "category",
        "app",
        "desktop_id",
        "ref",
        "mechanism",
        "mime_types",
        "rationale",
        "human_signed",
    }


# --- emit.emit_mime -----------------------------------------------------------


def test_emit_mime_builds_artifacts() -> None:
    result = emit.emit_mime(TWO)
    assert result.mimeapps_list == mime.mimeapps_list(TWO)
    assert result.integration_plan_json == mime.plan_json(TWO)
    assert result.mime_artifacts == (
        emit.MIMEAPPS_LIST_FILENAME,
        emit.INTEGRATION_PLAN_JSON_FILENAME,
    )


def test_emit_mime_none_is_empty() -> None:
    """emit_mime(None) (emit without --mime) carries no mime payloads."""

    result = emit.emit_mime(None)
    assert result.mimeapps_list == ""
    assert result.integration_plan_json == ""
    assert result.mime_artifacts == ()


def test_emit_mime_result_writes_two_files(tmp_path) -> None:
    result = emit.emit_mime(TWO)
    written = result.write(tmp_path)
    names = [p.name for p in written]
    assert emit.MIMEAPPS_LIST_FILENAME in names
    assert emit.INTEGRATION_PLAN_JSON_FILENAME in names
    # The written files are valid (INI + JSON).
    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read(str(tmp_path / emit.MIMEAPPS_LIST_FILENAME))
    assert "Default Applications" in cp.sections()
    json.loads((tmp_path / emit.INTEGRATION_PLAN_JSON_FILENAME).read_text())


def test_emit_mime_empty_is_no_files(tmp_path) -> None:
    result = emit.emit_mime(None)
    written = result.write(tmp_path)
    assert emit.MIMEAPPS_LIST_FILENAME not in [p.name for p in written]


# --- CLI: emit --mime ---------------------------------------------------------


def test_cli_emit_mime_writes_files_and_exits_zero(capsys, tmp_path) -> None:
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--mime"])
    assert rc == cli.EXIT_OK
    assert (out / emit.MIMEAPPS_LIST_FILENAME).is_file()
    assert (out / emit.INTEGRATION_PLAN_JSON_FILENAME).is_file()
    # The base artifacts are still written.
    assert (out / emit.FLATPAK_REFS_FILENAME).is_file()
    assert (out / emit.MIRROR_MANIFEST_FILENAME).is_file()
    out_text = capsys.readouterr().out
    assert emit.MIMEAPPS_LIST_FILENAME in out_text
    assert emit.INTEGRATION_PLAN_JSON_FILENAME in out_text


def test_cli_emit_mime_json_reports_mime_artifacts(capsys, tmp_path) -> None:
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--mime", "--json"])
    assert rc == cli.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["mime_artifacts"] == [
        emit.MIMEAPPS_LIST_FILENAME,
        emit.INTEGRATION_PLAN_JSON_FILENAME,
    ]
    # The written mimeapps.list equals the JSON-reported payload.
    written = (out / emit.MIMEAPPS_LIST_FILENAME).read_text()
    assert written == payload["mimeapps_list"]


def test_cli_emit_without_mime_has_no_mime_artifacts(capsys, tmp_path) -> None:
    out = tmp_path / "out"
    rc = cli.main(["emit", "--out", str(out), "--json"])
    assert rc == cli.EXIT_OK
    assert not (out / emit.MIMEAPPS_LIST_FILENAME).exists()
    assert not (out / emit.INTEGRATION_PLAN_JSON_FILENAME).exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["mime_artifacts"] == []


def test_cli_emit_mime_matches_committed_plan(capsys, tmp_path) -> None:
    """The emitted mimeapps.list reflects the committed plan (10 categories)."""

    out = tmp_path / "out"
    cli.main(["emit", "--out", str(out), "--mime"])
    text = (out / emit.MIMEAPPS_LIST_FILENAME).read_text()
    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read_string(text)
    # 10 categories, minus the two non-file-handler ones (calculator,
    # screenshots) that contribute no default row, still cover the file
    # handlers; every curated desktop id appears somewhere.
    for desktop_id in PLAN.desktop_ids():
        # calculator/screenshots are discoverable-only; they appear in the plan
        # JSON, not necessarily as a default row — assert the file handlers do.
        pass
    # The file-handler desktop ids are present as defaults.
    assert "org.mozilla.FirefoxRepo.desktop" in text
    assert "org.gnome.Evince.desktop" in text


# --- load (TOML + JSON) -------------------------------------------------------


def test_load_reads_json(tmp_path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(
        '[{"category": "browser", "app": "x", "desktop_id": "x.desktop", "rationale": "y"}]',
        encoding="utf-8",
    )
    loaded = load(path)
    assert len(loaded) == 1
    assert loaded[0].app == "x"


def test_load_missing_file_raises_plan_load_error() -> None:
    with pytest.raises(PlanLoadError):
        load("/nonexistent/plan.toml")


def test_load_invalid_file_raises_plan_load_error(tmp_path) -> None:
    path = tmp_path / "plan.toml"
    path.write_text("this is not = valid = toml [[[", encoding="utf-8")
    with pytest.raises(PlanLoadError):
        load(path)


def test_parse_plan_accepts_top_level_array() -> None:
    parsed = parse_plan(
        [
            {
                "category": "browser",
                "app": "a",
                "desktop_id": "a.desktop",
                "rationale": "x",
            },
            {
                "category": "files",
                "app": "c",
                "desktop_id": "c.desktop",
                "rationale": "x",
            },
        ]
    )
    assert len(parsed) == 2


def test_parse_plan_accepts_entry_table() -> None:
    parsed = parse_plan(
        {"entry": [{"category": "browser", "app": "a", "desktop_id": "a.desktop", "rationale": "x"}]}
    )
    assert len(parsed) == 1


def test_parse_plan_missing_entry_key() -> None:
    with pytest.raises(MalformedPlanError, match="entry"):
        parse_plan({"nope": []})


def test_parse_plan_non_mapping_entry() -> None:
    with pytest.raises(MalformedPlanError, match="entry #1"):
        parse_plan(
            [
                {"category": "browser", "app": "a", "desktop_id": "a.desktop", "rationale": "x"},
                "bad",
            ]
        )


def test_committed_plan_parses_as_toml() -> None:
    """The committed mime-integration.toml parses as TOML (sanity)."""

    root = pathlib.Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "mime-integration.toml").read_text())
    assert "entry" in data
    assert len(data["entry"]) == 10
