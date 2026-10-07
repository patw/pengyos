"""Tests for the lint rules (``pengyos_recipe_lint.rules``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pengyos_recipe_lint.recipe_parser import RecipeSource
from pengyos_recipe_lint.rules import (
    RULE_ORDER,
    HashEntry,
    RuleId,
    check_recipe,
    hash_present,
    license_declared,
    no_unverified_downloads,
    parse_hash_text,
    site_is_verified,
    version_pinned,
)
from pengyos_recipe_lint.recipes import Recipe


def _recipe(tmp_path: Path, name: str, text: str, hash_text: str | None = None) -> Recipe:
    """Write ``name.mk`` (+ optional ``name.hash``) and return its :class:`Recipe`."""
    mk = tmp_path / f"{name}.mk"
    mk.write_text(text, encoding="utf-8")
    if hash_text is not None:
        mk.with_suffix(".hash").write_text(hash_text, encoding="utf-8")
    return Recipe(path=mk)


def _source(text: str) -> RecipeSource:
    return RecipeSource.from_text(text)


# --------------------------------------------------------------------------- #
# version_pinned
# --------------------------------------------------------------------------- #
def test_version_pinned_ok_for_concrete_values(tmp_path):
    for value in ["1.2.3", "2026.09", "10.4.1-rc1", "  3.1.4  ", "'1.2.3'"]:
        text = f"FOO_VERSION = {value}"
        recipe = _recipe(tmp_path, "foo", text)
        result = version_pinned(recipe, _source(text))
        assert result.ok is True
        assert result.rule == RuleId.VERSION_PINNED
        assert result.line_no == 1


def test_version_pinned_missing(tmp_path):
    recipe = _recipe(tmp_path, "foo", "FOO_SITE = https://x")
    result = version_pinned(recipe, _source("FOO_SITE = https://x"))
    assert result.ok is False
    assert result.rule == RuleId.VERSION_PINNED
    assert "not set" in result.message
    assert result.hint


@pytest.mark.parametrize("value", ["git", "HEAD", "auto", "master", "main", "latest", "trunk", "current"])
def test_version_pinned_flags_auto_or_branch(tmp_path, value):
    text = f"FOO_VERSION = {value}"
    recipe = _recipe(tmp_path, "foo", text)
    result = version_pinned(recipe, _source(text))
    assert result.ok is False
    assert result.rule == RuleId.VERSION_PINNED
    assert result.line_no == 1


def test_version_pinned_flags_make_expansion(tmp_path):
    text = "FOO_VERSION = $(FOO)_VERSION"
    recipe = _recipe(tmp_path, "foo", text)
    result = version_pinned(recipe, _source(text))
    assert result.ok is False
    assert "build time" in result.message


def test_version_pinned_empty(tmp_path):
    text = "FOO_VERSION =   "
    recipe = _recipe(tmp_path, "foo", text)
    result = version_pinned(recipe, _source(text))
    assert result.ok is False
    assert "empty" in result.message
    assert result.line_no == 1


def test_version_pinned_line_no_tracks_last_assignment(tmp_path):
    text = "\n".join(["FOO_VERSION = 1.0", "FOO_VERSION = master"])
    recipe = _recipe(tmp_path, "foo", text)
    result = version_pinned(recipe, RecipeSource.from_text(text))
    assert result.line_no == 2


# --------------------------------------------------------------------------- #
# parse_hash_text
# --------------------------------------------------------------------------- #
def test_parse_hash_text_basic():
    entries = parse_hash_text(
        "\n".join(
            [
                "# comment line",
                "",
                "sha256  abcdef0123456789  foo-1.2.3.tar.gz",
                "sha512  deadbeef  foo-1.2.3.tar.gz",
            ]
        )
    )
    assert entries == [
        HashEntry("sha256", "abcdef0123456789", "foo-1.2.3.tar.gz", 3),
        HashEntry("sha512", "deadbeef", "foo-1.2.3.tar.gz", 4),
    ]


def test_parse_hash_text_ignores_comments_and_short_lines():
    entries = parse_hash_text("\n".join(["# c", "   ", "sha256  onlytwo", "x"]))
    assert entries == []


def test_parse_hash_text_case_insensitive_algorithm():
    entries = parse_hash_text("SHA256  abc  foo-1.2.3.tar.gz")
    assert len(entries) == 1
    assert entries[0].algorithm == "sha256"


# --------------------------------------------------------------------------- #
# hash_present
# --------------------------------------------------------------------------- #
def test_hash_present_missing_sibling(tmp_path):
    recipe = _recipe(tmp_path, "foo", "FOO_VERSION = 1.0")
    result = hash_present(recipe, _source("FOO_VERSION = 1.0"))
    assert result.ok is False
    assert result.rule == RuleId.HASH_PRESENT
    assert "no .hash" in result.message
    assert result.hint


def test_hash_present_sha256_for_source_ok(tmp_path):
    recipe = _recipe(
        tmp_path,
        "foo",
        "FOO_SOURCE = foo-1.2.3.tar.gz",
        hash_text="sha256  abcdef0123456789  foo-1.2.3.tar.gz\n",
    )
    result = hash_present(recipe, _source("FOO_SOURCE = foo-1.2.3.tar.gz"))
    assert result.ok is True
    assert result.line_no == 1


def test_hash_present_no_sha256_only_sha512(tmp_path):
    recipe = _recipe(
        tmp_path,
        "foo",
        "FOO_SOURCE = foo-1.2.3.tar.gz",
        hash_text="sha512  deadbeef  foo-1.2.3.tar.gz\n",
    )
    result = hash_present(recipe, _source("FOO_SOURCE = foo-1.2.3.tar.gz"))
    assert result.ok is False
    assert "no sha256" in result.message


def test_hash_present_sha256_not_for_source(tmp_path):
    recipe = _recipe(
        tmp_path,
        "foo",
        "FOO_SOURCE = foo-1.2.3.tar.gz",
        hash_text="sha256  abcdef0123456789  other-9.9.tar.gz\n",
    )
    result = hash_present(recipe, _source("FOO_SOURCE = foo-1.2.3.tar.gz"))
    assert result.ok is False
    assert "do not cover" in result.message


def test_hash_present_sha256_ok_when_source_absent(tmp_path):
    # Without _SOURCE we cannot check the filename, so any sha256 passes.
    recipe = _recipe(
        tmp_path,
        "foo",
        "FOO_VERSION = 1.0",
        hash_text="sha256  abcdef0123456789  whatever.tar.gz\n",
    )
    result = hash_present(recipe, _source("FOO_VERSION = 1.0"))
    assert result.ok is True


def test_hash_present_reads_real_file(tmp_path):
    # Sanity check that the rule reads the file from disk, not from text.
    recipe = _recipe(
        tmp_path,
        "foo",
        "FOO_SOURCE = foo-1.2.3.tar.gz",
        hash_text="sha256  abcdef0123456789  foo-1.2.3.tar.gz\n",
    )
    result = hash_present(recipe, _source("FOO_SOURCE = foo-1.2.3.tar.gz"))
    assert result.ok is True


# --------------------------------------------------------------------------- #
# license_declared
# --------------------------------------------------------------------------- #
def test_license_declared_ok(tmp_path):
    recipe = _recipe(tmp_path, "foo", "FOO_LICENSE = MIT")
    result = license_declared(recipe, _source("FOO_LICENSE = MIT"))
    assert result.ok is True
    assert result.rule == RuleId.LICENSE_DECLARED
    assert result.line_no == 1


def test_license_declared_missing(tmp_path):
    recipe = _recipe(tmp_path, "foo", "FOO_VERSION = 1.0")
    result = license_declared(recipe, _source("FOO_VERSION = 1.0"))
    assert result.ok is False
    assert "not declared" in result.message
    assert result.hint


def test_license_declared_empty(tmp_path):
    recipe = _recipe(tmp_path, "foo", "FOO_LICENSE =   ")
    result = license_declared(recipe, _source("FOO_LICENSE =   "))
    assert result.ok is False
    assert "empty" in result.message


def test_license_declared_matches_any_prefix(tmp_path):
    # A prefixed license var (e.g. PENGYOS_SIGNIFY_LICENSE) satisfies the rule.
    recipe = _recipe(tmp_path, "foo", "PENGYOS_SIGNIFY_LICENSE = GPL-2.0-only")
    result = license_declared(recipe, _source("PENGYOS_SIGNIFY_LICENSE = GPL-2.0-only"))
    assert result.ok is True


# --------------------------------------------------------------------------- #
# site_is_verified helper
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "site",
    [
        "https://github.com/uutils/coreutils",
        "https://sig.riseup.net/signify",
        "https://www.freedesktop.org/software/dbus",
        "https://example.org/foo-1.2.3/",
        "https://example.org:8443/path?q=1#frag",
        "HTTPS://example.com/x",  # scheme is case-insensitive
    ],
)
def test_site_is_verified_true_for_https(site):
    assert site_is_verified(site) is True


@pytest.mark.parametrize(
    "site",
    [
        "http://example.com/foo",
        "git://github.com/foo.git",
        "ftp://mirror.example.org/foo",
        "file:///tmp/foo",
        "not-a-url",
        "/relative/path",
        "example.com/no-scheme",
        "",
        "   ",
    ],
)
def test_site_is_verified_false_for_non_https(site):
    assert site_is_verified(site) is False


# --------------------------------------------------------------------------- #
# no_unverified_downloads
# --------------------------------------------------------------------------- #


def test_no_unverified_downloads_ok_for_https(tmp_path):
    text = "FOO_SITE = https://mirror.example.org/foo"
    recipe = _recipe(tmp_path, "foo", text)
    result = no_unverified_downloads(recipe, _source(text))
    assert result.ok is True
    assert result.rule == RuleId.NO_UNVERIFIED_DOWNLOADS
    assert result.line_no == 1


def test_no_unverified_downloads_missing_site(tmp_path):
    recipe = _recipe(tmp_path, "foo", "FOO_VERSION = 1.0")
    result = no_unverified_downloads(recipe, _source("FOO_VERSION = 1.0"))
    assert result.ok is False
    assert result.rule == RuleId.NO_UNVERIFIED_DOWNLOADS
    assert "not set" in result.message
    assert result.hint


def test_no_unverified_downloads_empty_site(tmp_path):
    text = "FOO_SITE =   "
    recipe = _recipe(tmp_path, "foo", text)
    result = no_unverified_downloads(recipe, _source(text))
    assert result.ok is False
    assert result.rule == RuleId.NO_UNVERIFIED_DOWNLOADS
    assert "empty" in result.message
    assert result.line_no == 1


@pytest.mark.parametrize("site", ["http://example.com/foo", "http://example.com:8080/foo"])
def test_no_unverified_downloads_flags_insecure_http(tmp_path, site):
    text = f"FOO_SITE = {site}"
    recipe = _recipe(tmp_path, "foo", text)
    result = no_unverified_downloads(recipe, _source(text))
    assert result.ok is False
    assert result.rule == RuleId.NO_UNVERIFIED_DOWNLOADS
    assert "insecure" in result.message
    assert result.line_no == 1


@pytest.mark.parametrize(
    "site",
    ["git://github.com/foo.git", "ftp://mirror.example.org/foo", "file:///tmp/foo"],
)
def test_no_unverified_downloads_flags_non_https_scheme(tmp_path, site):
    text = f"FOO_SITE = {site}"
    recipe = _recipe(tmp_path, "foo", text)
    result = no_unverified_downloads(recipe, _source(text))
    assert result.ok is False
    assert result.rule == RuleId.NO_UNVERIFIED_DOWNLOADS
    assert "scheme" in result.message


def test_no_unverified_downloads_flags_relative_url(tmp_path):
    text = "FOO_SITE = relative/path"
    recipe = _recipe(tmp_path, "foo", text)
    result = no_unverified_downloads(recipe, _source(text))
    assert result.ok is False
    assert result.rule == RuleId.NO_UNVERIFIED_DOWNLOADS
    assert "not an absolute URL" in result.message


def test_no_unverified_downloads_line_no_tracks_last_assignment(tmp_path):
    text = "FOO_SITE = https://a.example/foo\nFOO_SITE = http://b.example/foo\n"
    recipe = _recipe(tmp_path, "foo", text)
    result = no_unverified_downloads(recipe, RecipeSource.from_text(text))
    assert result.line_no == 2


def test_no_unverified_downloads_matches_any_prefix(tmp_path):
    # A prefixed site var (e.g. PENGYOS_SIGNIFY_SITE) satisfies the rule.
    text = "PENGYOS_SIGNIFY_SITE = https://sig.riseup.net/signify"
    recipe = _recipe(tmp_path, "foo", text)
    result = no_unverified_downloads(recipe, _source(text))
    assert result.ok is True


# --------------------------------------------------------------------------- #
# check_recipe driver
# --------------------------------------------------------------------------- #
def test_check_recipe_all_ok(tmp_path):
    text = "\n".join(
        [
            "FOO_VERSION = 1.2.3",
            "FOO_SITE = https://example.org/foo",
            "FOO_SOURCE = foo-1.2.3.tar.gz",
            "FOO_LICENSE = MIT",
        ]
    )
    recipe = _recipe(tmp_path, "perfect", text, hash_text="sha256  abcdef0123456789  foo-1.2.3.tar.gz\n")
    results = check_recipe(recipe, RecipeSource.from_file(recipe.path))
    assert all(r.ok for r in results)
    assert [r.rule for r in results] == list(RULE_ORDER)


def test_check_recipe_stable_order_and_mix(tmp_path):
    # A recipe that fails all four, in a deterministic order.
    text = "FOO_VERSION = HEAD\nFOO_SITE = not-a-url\n"
    recipe = _recipe(tmp_path, "bad", text)
    results = check_recipe(recipe, _source(text))
    assert [r.rule for r in results] == list(RULE_ORDER)
    assert all(not r.ok for r in results)


def test_check_recipe_is_deterministic(tmp_path):
    text = "FOO_SITE = https://x\n"
    recipe = _recipe(tmp_path, "bad", text)
    first = check_recipe(recipe, _source(text))
    second = check_recipe(recipe, _source(text))
    assert [r.message for r in first] == [r.message for r in second]
