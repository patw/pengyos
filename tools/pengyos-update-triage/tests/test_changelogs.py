"""Tests for the changelog input schema + loader.

Covers ``pengyos_update_triage.changelogs``: component attribution (filename
stem vs. ``component:`` directive), version-section parsing across the
recognised header styles, preamble/blank handling, and the structured errors.
Keyword *scanning* is a later subtask; here we only assert the parsed *shape*.
"""

from pathlib import Path

import pytest

from pengyos_update_triage.changelogs import (
    load_changelogs,
    parse_changelog_text,
    parse_version_sections,
)
from pengyos_update_triage.errors import ChangelogError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHG = FIXTURES / "changelogs"


def _by_component(changelogs):
    return {c.component: c for c in changelogs}


# --- directory loading over the committed fixtures --------------------------


def test_load_changelogs_discovers_all():
    changelogs = load_changelogs(CHG)
    comps = _by_component(changelogs)
    # glibc.txt / curl.txt declare a directive; openssl.txt / zlib.txt fall back
    # to the filename stem.
    assert set(comps) == {"glibc", "openssl", "curl", "zlib"}


def test_component_directive_overrides_stem(tmp_path):
    (tmp_path / "weird-name.txt").write_text(
        "# component: glibc\n\n## 1.0\n- hello\n", encoding="utf-8"
    )
    (tmp_path / "plain.txt").write_text("## 2.0\n- world\n", encoding="utf-8")
    comps = _by_component(load_changelogs(tmp_path))
    assert comps["glibc"].path.endswith("weird-name.txt")
    assert "plain" in comps  # fallback to the stem


def test_load_changelogs_missing_dir():
    with pytest.raises(ChangelogError) as exc:
        load_changelogs(CHG / "nope")
    assert "not found" in str(exc.value)


def test_load_changelogs_not_a_dir(tmp_path):
    f = tmp_path / "one.txt"
    f.write_text("## 1.0\n", encoding="utf-8")
    with pytest.raises(ChangelogError):
        load_changelogs(f)


def test_load_changelogs_empty_dir_is_legal(tmp_path):
    assert load_changelogs(tmp_path) == []


def test_load_changelogs_non_utf8(tmp_path):
    (tmp_path / "bad.txt").write_bytes(b"\xff\xfe\x00 not utf-8")
    with pytest.raises(ChangelogError) as exc:
        load_changelogs(tmp_path)
    assert "UTF-8" in str(exc.value)


# --- component attribution --------------------------------------------------


def test_directive_in_comment_form():
    chg = parse_changelog_text("# component: openssl\n\n## 3.0\n- x\n", "fallback")
    assert chg.component == "openssl"


def test_directive_with_at_hash():
    chg = parse_changelog_text("@component: zlib\n\n## 1.3\n- x\n", "fallback")
    assert chg.component == "zlib"


def test_no_directive_uses_default_component():
    chg = parse_changelog_text("## 1.0\n- x\n", "openssl")
    assert chg.component == "openssl"


def test_directive_is_not_mistaken_for_version_header():
    # "component:" is not a version; the following real header must still work.
    chg = parse_changelog_text("# component: glibc\n\n## 2.40.0\n- fix\n", "x")
    assert chg.component == "glibc"
    assert [s.version for s in chg.sections] == ["2.40.0"]


# --- version-section parsing ------------------------------------------------


def test_markdown_header_style():
    sections = parse_version_sections("## 2.40.0\n- fix CVE-1\n\n## 2.39.0\n- x\n")
    assert [s.version for s in sections] == ["2.40.0", "2.39.0"]
    assert sections[0].entries == ["- fix CVE-1"]
    assert sections[0].header_line == "## 2.40.0"


def test_version_label_style():
    sections = parse_version_sections("Version 3.0.14\n* Security fix\n")
    assert [s.version for s in sections] == ["3.0.14"]


def test_debian_double_equals_style():
    sections = parse_version_sections("== 1.3.1 ==\n* fix\n== 1.2.13 ==\n* y\n")
    assert [s.version for s in sections] == ["1.3.1", "1.2.13"]


def test_preamble_before_first_header_is_ignored():
    sections = parse_version_sections(
        "Some intro line\nAnother intro line\n\n## 1.0.0\n- real entry\n"
    )
    assert len(sections) == 1
    assert sections[0].version == "1.0.0"
    assert sections[0].entries == ["- real entry"]


def test_blank_lines_not_recorded_as_entries():
    sections = parse_version_sections("## 1.0\n- a\n\n   \n- b\n")
    assert sections[0].entries == ["- a", "- b"]


def test_a_mention_of_version_is_not_a_header():
    # A bullet that mentions a version in its body is not a section break.
    text = "## 1.0\n- upgrade to curl 8.9.0 for the fix\n"
    sections = parse_version_sections(text)
    assert len(sections) == 1
    assert sections[0].version == "1.0"
    assert any("upgrade to curl 8.9.0 for the fix" in e for e in sections[0].entries)


def test_no_version_at_all_yields_no_sections():
    assert parse_version_sections("just prose\nmore prose\n") == []


def test_header_with_date_annotation():
    sections = parse_version_sections("## 1.2.3 (2024-01-01)\n- x\n")
    assert sections[0].version == "1.2.3"


def test_header_with_v_prefix():
    sections = parse_version_sections("## v2.1\n- x\n")
    assert sections[0].version == "2.1"


# --- committed fixture spot-checks ------------------------------------------


def test_glibc_fixture_sections():
    chg = _by_component(load_changelogs(CHG))["glibc"]
    versions = [s.version for s in chg.sections]
    assert versions == ["2.40.0", "2.39.0", "2.38.0"]
    # the CVE entry is captured under 2.40.0
    top = chg.sections[0]
    assert any("CVE-2023-4911" in e for e in top.entries)


def test_openssl_fixture_component_from_stem():
    chg = _by_component(load_changelogs(CHG))["openssl"]
    versions = [s.version for s in chg.sections]
    assert versions == ["3.0.14", "3.0.13"]


def test_zlib_fixture_double_equals():
    chg = _by_component(load_changelogs(CHG))["zlib"]
    versions = [s.version for s in chg.sections]
    assert versions == ["1.3.1", "1.2.13"]


def test_changelog_retains_raw_text():
    chg = _by_component(load_changelogs(CHG))["curl"]
    assert "CVE-2024-2392" in chg.text
    assert chg.path.endswith("curl.txt")
