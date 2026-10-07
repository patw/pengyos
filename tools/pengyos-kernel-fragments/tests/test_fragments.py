"""Tests for the fragment file format + tolerant parser (spec.md)."""

from __future__ import annotations

import pytest

from pengyos_kernel_fragments.fragments import (
    ConfigEntry,
    Fragment,
    ParseErrorRecord,
    ValueKind,
    load_directory,
    parse_fragment_file,
    parse_fragment_text,
)


def _symbols(frag: Fragment) -> dict[str, ConfigEntry]:
    return frag.values


# --------------------------------------------------------------------------- #
# Format + value kinds
# --------------------------------------------------------------------------- #


def test_parses_y_m_and_string_values():
    frag = parse_fragment_text(
        "\n".join(
            [
                "CONFIG_VIRTIO=y",
                "CONFIG_EXT4_FS=m",
                'CONFIG_KCMD="default"',
            ]
        ),
        name="f.fragment",
    )
    assert frag.is_clean
    assert frag.values["CONFIG_VIRTIO"] == ConfigEntry(
        "CONFIG_VIRTIO", "y", ValueKind.YES, 1, "f.fragment"
    )
    assert frag.values["CONFIG_EXT4_FS"].kind is ValueKind.MODULE
    assert frag.values["CONFIG_KCMD"].kind is ValueKind.STRING
    assert frag.values["CONFIG_KCMD"].value == "default"


def test_value_kind_enum_equals_its_string():
    assert ValueKind.YES == "y"
    assert ValueKind.MODULE == "m"
    assert ValueKind.STRING == "s"
    assert ValueKind.NO == "n"


def test_n_is_a_valid_disabled_value():
    frag = parse_fragment_text("CONFIG_FOO=n", name="f.fragment")
    assert frag.values["CONFIG_FOO"].kind is ValueKind.NO
    assert frag.is_clean


def test_config_entry_str_roundtrips():
    assert str(ConfigEntry("CONFIG_X", "y", ValueKind.YES, 1, "f")) == "CONFIG_X=y"
    assert (
        str(ConfigEntry("CONFIG_X", "v", ValueKind.STRING, 1, "f"))
        == 'CONFIG_X="v"'
    )


# --------------------------------------------------------------------------- #
# Tolerance: comments, blanks, whitespace
# --------------------------------------------------------------------------- #


def test_skips_full_line_comments_and_blank_lines():
    text = "\n".join(
        [
            "# a comment",
            "",
            "   ",
            "\t",
            "    # indented comment",
            "CONFIG_FOO=y",
        ]
    )
    frag = parse_fragment_text(text, name="f.fragment")
    assert frag.is_clean
    assert list(frag.values) == ["CONFIG_FOO"]


def test_strips_inline_comments():
    frag = parse_fragment_text(
        "CONFIG_FOO=y   # set it at boot", name="f.fragment"
    )
    assert frag.values["CONFIG_FOO"].value == "y"
    assert frag.is_clean


def test_hash_inside_quoted_string_is_preserved():
    frag = parse_fragment_text(
        'CONFIG_CMDLINE="root=/dev/sda1 quiet # not a comment"',
        name="f.fragment",
    )
    assert (
        frag.values["CONFIG_CMDLINE"].value
        == "root=/dev/sda1 quiet # not a comment"
    )


def test_tolerates_surrounding_whitespace():
    frag = parse_fragment_text("   CONFIG_FOO   =   y   ", name="f.fragment")
    assert frag.values["CONFIG_FOO"].value == "y"
    assert frag.is_clean


def test_leading_whitespace_before_symbol_ok():
    frag = parse_fragment_text("\t\tCONFIG_FOO=y", name="f.fragment")
    assert frag.values["CONFIG_FOO"].symbol == "CONFIG_FOO"


def test_empty_text_yields_clean_empty_fragment():
    frag = parse_fragment_text("", name="f.fragment")
    assert frag.entries == []
    assert frag.errors == []
    assert frag.is_clean


# --------------------------------------------------------------------------- #
# Malformed lines are recorded, never raised
# --------------------------------------------------------------------------- #


def test_records_line_without_equals():
    frag = parse_fragment_text("CONFIG_FOO y\nCONFIG_BAR=y", name="f.fragment")
    assert len(frag.errors) == 1
    err = frag.errors[0]
    assert isinstance(err, ParseErrorRecord)
    assert err.line_no == 1
    assert "no '='" in err.reason
    assert not frag.is_clean


def test_records_lowercase_symbol_as_error():
    frag = parse_fragment_text("CONFIG_bad=y", name="f.fragment")
    assert len(frag.errors) == 1
    assert "valid CONFIG_" in frag.errors[0].reason


def test_records_unrecognized_value():
    frag = parse_fragment_text("CONFIG_FOO=99", name="f.fragment")
    assert len(frag.errors) == 1
    assert "unrecognized value" in frag.errors[0].reason


def test_parser_never_raises_on_bad_input():
    bad = "\n".join(
        ["garbage", "CONFIG_X=99", "no_equals_here", "lower=y", "CONFIG_Y="]
    )
    frag = parse_fragment_text(bad, name="bad.fragment")
    # Every line that is not blank/comment is an error; nothing was raised.
    assert frag.errors
    assert all(isinstance(e, ParseErrorRecord) for e in frag.errors)


def test_trailing_empty_equals_is_an_error():
    frag = parse_fragment_text("CONFIG_FOO=", name="f.fragment")
    assert len(frag.errors) == 1


# --------------------------------------------------------------------------- #
# Intra-file precedence
# --------------------------------------------------------------------------- #


def test_last_assignment_wins_within_a_file():
    frag = parse_fragment_text(
        "\n".join(["CONFIG_DUP=y", "CONFIG_DUP=n", "CONFIG_DUP=m"]),
        name="f.fragment",
    )
    assert len(frag.entries) == 3
    assert frag.values["CONFIG_DUP"].value == "m"
    assert len(frag.values) == 1


def test_values_property_is_a_dict():
    frag = parse_fragment_text("CONFIG_A=y\nCONFIG_B=m", name="f.fragment")
    assert isinstance(frag.values, dict)
    assert set(frag.values) == {"CONFIG_A", "CONFIG_B"}


# --------------------------------------------------------------------------- #
# File + directory loading
# --------------------------------------------------------------------------- #


def test_parse_fragment_file_reads_from_disk(tmp_path):
    p = tmp_path / "kernel.fragment"
    p.write_text("CONFIG_FOO=y\n# c\nCONFIG_BAR=m\n", encoding="utf-8")
    frag = parse_fragment_file(p)
    assert frag.name == "kernel.fragment"
    assert frag.values["CONFIG_FOO"].value == "y"
    assert frag.values["CONFIG_BAR"].kind is ValueKind.MODULE
    assert frag.is_clean


def test_parse_fragment_file_records_disk_errors(tmp_path):
    p = tmp_path / "broken.fragment"
    p.write_text("CONFIG_FOO=oops\n", encoding="utf-8")
    frag = parse_fragment_file(p)
    assert not frag.is_clean
    assert frag.errors[0].line_no == 1


def test_load_directory_sorted_and_recurses(tmp_path):
    (tmp_path / "b.fragment").write_text("CONFIG_B=y\n", encoding="utf-8")
    (tmp_path / "a.fragment").write_text("CONFIG_A=y\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.fragment").write_text("CONFIG_C=y\n", encoding="utf-8")
    # Non-fragment files must be ignored, not choke the loader.
    (tmp_path / "README.md").write_text("not a fragment", encoding="utf-8")
    (tmp_path / "profile.txt").write_text("profile", encoding="utf-8")

    frags = load_directory(tmp_path)
    assert [f.name for f in frags] == ["a.fragment", "b.fragment", "c.fragment"]
    symbols = {e.symbol for f in frags for e in f.entries}
    assert symbols == {"CONFIG_A", "CONFIG_B", "CONFIG_C"}


def test_load_directory_empty(tmp_path):
    assert load_directory(tmp_path) == []
