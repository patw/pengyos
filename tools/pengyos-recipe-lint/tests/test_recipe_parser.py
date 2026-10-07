"""Tests for the tolerant ``.mk`` parser (``pengyos_recipe_lint.recipe_parser``)."""

from __future__ import annotations

import pytest

from pengyos_recipe_lint.recipe_parser import INTERESTED_SUFFIXES, RecipeSource


# --------------------------------------------------------------------------- #
# Happy path: the five variables the linter cares about.
# --------------------------------------------------------------------------- #
def test_parses_all_five_interested_vars():
    src = RecipeSource.from_text(
        "\n".join(
            [
                "FOO_VERSION = 1.2.3",
                "FOO_SITE = https://example.com/foo",
                "FOO_SOURCE = foo-1.2.3.tar.gz",
                "FOO_LICENSE = MIT",
                "FOO_LICENSE_FILES = COPYING",
            ]
        )
    )
    interested = src.interested()
    assert set(interested) == set(INTERESTED_SUFFIXES)
    assert interested["_VERSION"].value == "1.2.3"
    assert interested["_SITE"].value == "https://example.com/foo"
    assert interested["_SOURCE"].value == "foo-1.2.3.tar.gz"
    assert interested["_LICENSE"].value == "MIT"
    assert interested["_LICENSE_FILES"].value == "COPYING"


def test_each_interested_suffix_is_reported():
    assert INTERESTED_SUFFIXES == (
        "_VERSION",
        "_SITE",
        "_SOURCE",
        "_LICENSE",
        "_LICENSE_FILES",
    )


def test_get_exact_name():
    src = RecipeSource.from_text("FOO_VERSION = 9.9\nFOO_SITE = https://x")
    assert src.get("FOO_VERSION") == "9.9"
    assert src.get("FOO_SITE") == "https://x"
    assert src.get("NOPE") is None


def test_get_by_suffix_matches_any_prefix():
    src = RecipeSource.from_text("MY_PKG_LICENSE = GPL-2.0-only")
    var = src.get_by_suffix("_LICENSE")
    assert var is not None
    assert var.value == "GPL-2.0-only"
    assert var.name == "MY_PKG_LICENSE"


def test_license_and_license_files_do_not_collide():
    src = RecipeSource.from_text(
        "\n".join(["FOO_LICENSE = MIT", "FOO_LICENSE_FILES = COPYING"])
    )
    # _LICENSE must not match _LICENSE_FILES, and vice versa.
    assert src.get_by_suffix("_LICENSE").name == "FOO_LICENSE"
    assert src.get_by_suffix("_LICENSE_FILES").name == "FOO_LICENSE_FILES"


# --------------------------------------------------------------------------- #
# Last-assignment-wins semantics.
# --------------------------------------------------------------------------- #
def test_last_assignment_wins():
    src = RecipeSource.from_text("\n".join(["FOO_VERSION = 1.0", "FOO_VERSION = 2.0"]))
    assert src.get("FOO_VERSION") == "2.0"
    # And the line number reflects the last assignment.
    assert src.get_var("FOO_VERSION").line_no == 2


# --------------------------------------------------------------------------- #
# Operators.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "line,operator",
    [
        ("FOO = 1", "="),
        ("FOO := 1", ":="),
        ("FOO ?= 1", "?="),
        ("FOO += 1", "+="),
        ("FOO != 1", "!="),
    ],
)
def test_operators(line, operator):
    src = RecipeSource.from_text(line)
    var = src.get_var("FOO")
    assert var is not None
    assert var.operator == operator
    assert var.value == "1"


def test_override_and_export_prefixes():
    src = RecipeSource.from_text("\n".join(["override FOO = 1", "export BAR = 2"]))
    assert src.get("FOO") == "1"
    assert src.get("BAR") == "2"


# --------------------------------------------------------------------------- #
# Tolerance: comments, whitespace, directives.
# --------------------------------------------------------------------------- #
def test_ignores_full_line_comments():
    src = RecipeSource.from_text("\n".join(["# a comment", "   # indented comment"]))
    assert src.variables == ()


def test_strips_inline_comments():
    src = RecipeSource.from_text("FOO_VERSION = 1.2.3   # the release version")
    assert src.get("FOO_VERSION") == "1.2.3"


def test_embedded_hash_in_url_is_preserved():
    src = RecipeSource.from_text("FOO_SITE = https://example.com/p#frag")
    assert src.get("FOO_SITE") == "https://example.com/p#frag"


def test_leading_whitespace_is_tolerated():
    src = RecipeSource.from_text("    FOO_VERSION = 3.2.1")
    assert src.get("FOO_VERSION") == "3.2.1"


def test_ignores_directives_and_noise():
    src = RecipeSource.from_text(
        "\n".join(
            [
                "include $(sort $(wildcard $(BR2_EXTERNAL_PENGYOS_PATH)/package/*/*.mk))",
                "ifeq ($(FOO),bar)",
                "  BAZ = 1",
                "endif",
                "endef",
            ]
        )
    )
    # Only the real assignment inside the ifeq survives.
    assert src.get("BAZ") == "1"
    assert src.get("include") is None


def test_define_block_is_ignored_without_crashing():
    src = RecipeSource.from_text(
        "\n".join(["define FOO_EXTRA", "line one", "line two", "endef"])
    )
    assert src.variables == ()


def test_empty_and_blank_text():
    assert RecipeSource.from_text("").variables == ()
    assert RecipeSource.from_text("\n\n   \n").variables == ()


# --------------------------------------------------------------------------- #
# Quotes and continuation lines.
# --------------------------------------------------------------------------- #
def test_strips_surrounding_quotes():
    src = RecipeSource.from_text('FOO_VERSION = "1.2.3"')
    assert src.get("FOO_VERSION") == "1.2.3"
    src_single = RecipeSource.from_text("FOO_VERSION = '1.2.3'")
    assert src_single.get("FOO_VERSION") == "1.2.3"


def test_continuation_lines_joined():
    # GNU Make replaces a trailing backslash-newline with a single space, so a
    # value split across two lines is joined with one space.
    src = RecipeSource.from_text(
        "\n".join(["FOO_SITE = https://example.com/very/\\", "    long/path/"])
    )
    assert src.get("FOO_SITE") == "https://example.com/very/ long/path/"
    # The continuation counts as a single logical assignment on line 1.
    assert src.get_var("FOO_SITE").line_no == 1


def test_continuation_without_trailing_space_is_preserved():
    # A value that does not end in a space before the backslash joins directly.
    src = RecipeSource.from_text(
        "\n".join(["FOO_VERSION = 1.\\", "2.3"])
    )
    assert src.get("FOO_VERSION") == "1. 2.3"


def test_continuation_stops_at_comment_line():
    src = RecipeSource.from_text(
        "\n".join(["FOO_VERSION = 1.0", "# a comment", "FOO_SITE = https://x"])
    )
    assert src.get("FOO_VERSION") == "1.0"
    assert src.get("FOO_SITE") == "https://x"


def test_trailing_backslash_in_comment_does_not_continue():
    src = RecipeSource.from_text(
        "\n".join(["FOO_VERSION = 1.0 # note \\", "FOO_SITE = x"])
    )
    assert src.get("FOO_VERSION") == "1.0"
    assert src.get("FOO_SITE") == "x"


# --------------------------------------------------------------------------- #
# Line numbers.
# --------------------------------------------------------------------------- #
def test_line_numbers_are_1_based_and_accurate():
    src = RecipeSource.from_text(
        "\n".join(
            [
                "# header",  # line 1 (comment)
                "",  # line 2
                "FOO_VERSION = 1.0",  # line 3
                "FOO_LICENSE = MIT",  # line 4
            ]
        )
    )
    assert src.get_var("FOO_VERSION").line_no == 3
    assert src.line_by_suffix("_LICENSE") == 4


# --------------------------------------------------------------------------- #
# Filesystem I/O.
# --------------------------------------------------------------------------- #
def test_from_file_reads_from_disk(tmp_path):
    mk = tmp_path / "foo.mk"
    mk.write_text("FOO_VERSION = 4.5.6\n", encoding="utf-8")
    src = RecipeSource.from_file(mk)
    assert src.path.name == "foo.mk"
    assert src.get("FOO_VERSION") == "4.5.6"


def test_from_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        RecipeSource.from_file(tmp_path / "does-not-exist.mk")


def test_from_text_defaults_path_to_string():
    src = RecipeSource.from_text("FOO_VERSION = 1")
    assert str(src.path) == "<string>"
