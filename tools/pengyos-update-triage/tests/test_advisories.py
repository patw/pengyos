"""Tests for the advisory (OSV JSON) schema + loader.

Covers ``pengyos_update_triage.advisories``: parsing each supported wire shape
(single object / top-level array / ``{"results": [...]}``), the per-field
normalisation, provenance (``source_file``), and the structured errors for
format and schema violations.
"""

import json
from pathlib import Path

import pytest

from pengyos_update_triage.advisories import (
    load_advisories,
    parse_advisories,
    parse_advisory,
)
from pengyos_update_triage.errors import (
    AdvisoryError,
    AdvisoryFormatError,
    AdvisorySchemaError,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ADV = FIXTURES / "advisories"
ADV_BAD = FIXTURES / "advisories_bad"


# --- directory loading over the clean fixture set ---------------------------


def test_load_advisories_discovers_all_valid_fixtures():
    advisories = load_advisories(ADV)
    ids = {a.id for a in advisories}
    # single-object files
    assert "CVE-2024-4741" in ids
    assert "CVE-2023-4911" in ids
    # two advisories from the top-level array file
    assert "CVE-2024-2392" in ids
    assert "CVE-2023-38545" in ids
    # one advisory from the {"results": [...]} file
    assert "CVE-2023-45853" in ids
    # and the non-pinned one
    assert "CVE-2019-20079" in ids
    # 6 advisories total across 5 files (curl file holds two).
    assert len(advisories) == 6


def test_load_advisories_records_source_file():
    advisories = load_advisories(ADV)
    by_id = {a.id: a for a in advisories}
    glibc = by_id["CVE-2023-4911"]
    assert glibc.source_file is not None
    assert glibc.source_file.endswith("glibc_cve.json")


def test_load_advisories_ignores_non_json_files(tmp_path):
    # A stray .txt file in the advisories dir must be skipped, not error.
    real = list(ADV.glob("*.json"))
    assert real, "expected json fixtures to exist"
    (tmp_path / "notes.txt").write_text("not an advisory", encoding="utf-8")
    for f in real:
        (tmp_path / f.name).write_bytes(f.read_bytes())
    advisories = load_advisories(tmp_path)
    assert len(advisories) == 6


def test_load_advisories_missing_dir():
    with pytest.raises(AdvisoryError) as exc:
        load_advisories(ADV / "no-such-dir")
    assert "not found" in str(exc.value)


def test_load_advisories_path_not_a_dir(tmp_path):
    f = tmp_path / "a.json"
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(AdvisoryError):
        load_advisories(f)


def test_load_advisories_empty_dir_is_legal(tmp_path):
    assert load_advisories(tmp_path) == []


# --- single advisory parsing ------------------------------------------------


def test_single_object_advisory_fields():
    adv = {a.id: a for a in load_advisories(ADV)}["CVE-2024-4741"]
    assert adv.aliases == []
    assert adv.summary
    assert adv.published is not None
    assert len(adv.severities) == 1
    assert adv.severities[0].type == "CVSS_V3"
    assert adv.severities[0].score.startswith("CVSS:3.1")
    assert len(adv.affected) == 1
    assert adv.affected[0].package_name == "openssl"
    assert adv.affected[0].ecosystem == "alpine"
    rng = adv.affected[0].ranges[0]
    assert rng.type == "ECOSYSTEM"
    assert [e for e in rng.events if e.fixed == "3.0.14"], "fixed event present"
    assert any(e.introduced == "0" for e in rng.events)
    assert adv.references and adv.references[0].startswith("https://")


def test_glibc_advisory_has_explicit_versions_and_range():
    adv = {a.id: a for a in load_advisories(ADV)}["CVE-2023-4911"]
    aff = adv.affected[0]
    assert "2.39" in aff.versions
    # a fixed=2.40 range event exists
    fixed = [e.fixed for e in aff.ranges[0].events if e.fixed is not None]
    assert "2.40" in fixed


def test_results_list_shape_parsed():
    adv = {a.id: a for a in load_advisories(ADV)}["CVE-2023-45853"]
    aff = adv.affected[0]
    assert aff.package_name == "zlib"
    # last_affected is preserved.
    last = [e.last_affected for e in aff.ranges[0].events if e.last_affected]
    assert last == ["1.3.1"]


def test_array_shape_parsed_into_two():
    advs = load_advisories(ADV)
    curl = [a for a in advs if a.affected and a.affected[0].package_name == "curl"]
    assert {a.id for a in curl} == {"CVE-2024-2392", "CVE-2023-38545"}


# --- parse_advisory / parse_advisories (pure) -------------------------------


def test_parse_advisory_minimal_object():
    adv = parse_advisory({"id": "CVE-1"}, source_file="x.json")
    assert adv.id == "CVE-1"
    assert adv.summary == ""
    assert adv.affected == []
    assert adv.source_file == "x.json"


def test_parse_advisories_from_list():
    advs = parse_advisories([{"id": "A"}, {"id": "B"}], source_file="y.json")
    assert [a.id for a in advs] == ["A", "B"]
    assert all(a.source_file == "y.json" for a in advs)


def test_parse_advisories_from_results_dict():
    advs = parse_advisories({"results": [{"id": "R1"}, {"id": "R2"}]})
    assert [a.id for a in advs] == ["R1", "R2"]


def test_parse_advisory_rejects_non_object():
    with pytest.raises(AdvisorySchemaError):
        parse_advisory(["not", "an", "object"])


def test_parse_advisory_requires_id():
    with pytest.raises(AdvisorySchemaError):
        parse_advisory({"summary": "no id"})


def test_parse_advisory_requires_nonempty_id():
    with pytest.raises(AdvisorySchemaError):
        parse_advisory({"id": "   "})


def test_parse_advisories_rejects_bad_toplevel():
    with pytest.raises(AdvisorySchemaError):
        parse_advisories("just a string")


# --- schema violations (bad fixture dir) ------------------------------------


def test_bad_dir_raises_schema_error():
    # Each bad fixture is its own schema violation; loading the whole bad dir
    # must surface a structured AdvisorySchemaError (first bad file in sort
    # order is affected_no_name.json).
    with pytest.raises(AdvisorySchemaError) as exc:
        load_advisories(ADV_BAD)
    assert exc.value.path is not None


def test_no_id_file():
    with pytest.raises(AdvisorySchemaError):
        parse_advisories(json.loads((ADV_BAD / "no_id.json").read_text(encoding="utf-8")))


def test_affected_without_name():
    with pytest.raises(AdvisorySchemaError):
        parse_advisories(
            json.loads((ADV_BAD / "affected_no_name.json").read_text(encoding="utf-8"))
        )


def test_empty_range_event():
    with pytest.raises(AdvisorySchemaError):
        parse_advisories(
            json.loads((ADV_BAD / "empty_event.json").read_text(encoding="utf-8"))
        )


def test_invalid_json_file_direct(tmp_path):
    (tmp_path / "bad.json").write_bytes((ADV_BAD / "not_json.json").read_bytes())
    with pytest.raises(AdvisoryFormatError) as exc:
        load_advisories(tmp_path)
    assert exc.value.path is not None
    assert "JSON" in str(exc.value)


# --- severity / references shape guards -------------------------------------


def test_severity_entry_requires_type_and_score():
    with pytest.raises(AdvisorySchemaError):
        parse_advisory({"id": "X", "severity": [{"type": "CVSS_V3"}]})
    with pytest.raises(AdvisorySchemaError):
        parse_advisory({"id": "X", "severity": [{"score": "s"}]})


def test_references_accept_objects_and_strings():
    adv = parse_advisory(
        {
            "id": "X",
            "references": [
                {"url": "https://example.com/a"},
                "https://example.com/b",
            ],
        }
    )
    assert adv.references == ["https://example.com/a", "https://example.com/b"]


def test_all_identifiers_used_as_evidence():
    adv = {a.id: a for a in load_advisories(ADV)}["CVE-2024-4741"]
    ids = adv.all_identifiers()
    assert ids == ["CVE-2024-4741"]
    glibc = {a.id: a for a in load_advisories(ADV)}["CVE-2023-4911"]
    # A curated advisory that carries a GHSA cross-reference lists both.
    assert glibc.all_identifiers() == ["CVE-2023-4911", "GHSA-m77w-6vjw-wh2f"]
