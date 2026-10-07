"""Tests for the component manifest (``pengyos_cosmic_pkg.manifest``).

These cover the data model, the schema (shape) validation, the semantic
validation (git-URL form, pinned-reference rule, name hygiene), the loader
(``load_manifest`` / ``parse_manifest``), and the committed example manifest.
Everything is hermetic — no network, root, or hardware.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from pengyos_cosmic_pkg.manifest import (
    Dependency,
    ManifestError,
    is_pinned,
    load_manifest,
    parse_manifest,
    component_table,
    dependency_table,
)

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# is_pinned — the shared pinned-reference predicate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reference",
    [
        "v0.6.0",
        "0.6.0",
        "v1",
        "1",
        "v0",
        "1.2.3-rc.1",
        "v0.6.0-beta",
        "v0.6.0-rc.1",
        "abcdef0123456789",  # full SHA
        "abc1234",  # short SHA
    ],
)
def test_is_pinned_accepts_concrete_references(reference: str) -> None:
    assert is_pinned(reference)


@pytest.mark.parametrize(
    "reference",
    [
        "main",
        "master",
        "stable",
        "latest",
        "dev",
        "develop",
        "release",
        "HEAD",
        "v",  # a bare 'v' with no version is not pinned
        "",
        "   ",
    ],
)
def test_is_pinned_rejects_floating_references(reference: str) -> None:
    assert not is_pinned(reference)


# --------------------------------------------------------------------------- #
# Parsing a valid manifest
# --------------------------------------------------------------------------- #


def test_parse_full_manifest() -> None:
    doc = {
        "name": "cosmic-text",
        "git_url": "https://github.com/pop-os/libcosmic",
        "tag": "v0.6.0",
        "license": "MIT OR Apache-2.0",
        "features": ["default", "render"],
        "binaries": ["cosmic-text"],
        "deps": [
            dependency_table(name="smithay-clipboard"),
            {
                "name": "winit",
                "git_url": "https://github.com/pop-os/winit",
                "tag": "v0.2.30",
            },
        ],
    }
    m = parse_manifest(doc)

    assert m.name == "cosmic-text"
    assert m.git_url == "https://github.com/pop-os/libcosmic"
    assert m.tag == "v0.6.0"
    assert m.license == "MIT OR Apache-2.0"
    assert m.features == ("default", "render")
    assert m.binaries == ("cosmic-text",)
    assert len(m.deps) == 2
    assert m.deps[0] == Dependency(
        "smithay-clipboard",
        "https://github.com/pop-os/smithay-clipboard",
        "v0.6.0",
    )
    assert m.deps[1].name == "winit"
    assert m.deps[1].tag == "v0.2.30"


def test_version_property_returns_tag() -> None:
    m = parse_manifest(component_table(tag="v1.2.3"))
    assert m.version == "v1.2.3"


def test_parse_minimal_manifest_defaults() -> None:
    m = parse_manifest(
        {
            "name": "libcosmic",
            "git_url": "https://github.com/pop-os/libcosmic",
            "tag": "v0.6.0",
            "license": "MIT",
        }
    )
    assert m.features == ()
    assert m.binaries == ()
    assert m.deps == ()
    assert not m.has_features()
    assert not m.has_binaries()
    assert not m.has_deps()


def test_has_helpers() -> None:
    doc = component_table(features=["a"], binaries=["b"])
    doc["deps"] = [dependency_table()]
    m = parse_manifest(doc)
    assert m.has_features()
    assert m.has_binaries()
    assert m.has_deps()


def test_features_and_binaries_are_deduplicated_and_ordered() -> None:
    m = parse_manifest(
        component_table(features=["b", "a", "b", "c", "a"], binaries=["x"])
    )
    assert m.features == ("b", "a", "c")


def test_ssh_git_url_is_accepted() -> None:
    doc = component_table(git_url="git@github.com:pop-os/libcosmic.git")
    m = parse_manifest(doc)
    assert m.git_url == "git@github.com:pop-os/libcosmic.git"


def test_dep_as_single_table_is_accepted() -> None:
    # A single [[deps]] table (not wrapped in a list) is tolerated.
    doc = component_table()
    doc["deps"] = {"name": "winit",
                   "git_url": "https://github.com/pop-os/winit",
                   "tag": "v0.2.30"}
    m = parse_manifest(doc)
    assert len(m.deps) == 1
    assert m.deps[0].name == "winit"


# --------------------------------------------------------------------------- #
# Shape validation — required fields, types, unknown fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("missing", ["name", "git_url", "tag", "license"])
def test_missing_required_field_raises(missing: str) -> None:
    doc = component_table()
    del doc[missing]
    with pytest.raises(ManifestError, match=f"missing required field '{missing}'"):
        parse_manifest(doc)


def test_unknown_root_field_raises() -> None:
    doc = component_table()
    doc["bogus"] = "nope"
    with pytest.raises(ManifestError, match="unknown field\\(s\\): bogus"):
        parse_manifest(doc)


def test_unknown_dep_field_raises() -> None:
    doc = component_table()
    doc["deps"] = [
        {"name": "winit", "git_url": "https://x", "tag": "v1", "bogus": 1}
    ]
    with pytest.raises(ManifestError, match="deps\\[0\\].*unknown field\\(s\\): bogus"):
        parse_manifest(doc)


def test_non_string_field_raises() -> None:
    doc = component_table()
    doc["tag"] = 6  # type: ignore[assignment]
    with pytest.raises(ManifestError, match="expected a string"):
        parse_manifest(doc)


def test_empty_string_field_raises() -> None:
    doc = component_table()
    doc["name"] = "   "
    with pytest.raises(ManifestError, match="must not be empty"):
        parse_manifest(doc)


def test_features_wrong_type_raises() -> None:
    doc = component_table()
    doc["features"] = "default"  # a string, not an array
    with pytest.raises(ManifestError, match="expected an array of strings"):
        parse_manifest(doc)


def test_features_non_string_item_raises() -> None:
    doc = component_table()
    doc["features"] = ["default", 3]  # type: ignore[list-item]
    with pytest.raises(ManifestError, match="expected strings"):
        parse_manifest(doc)


# --------------------------------------------------------------------------- #
# Semantic validation — name hygiene, git URL, pinned reference
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    ["cosmic-text", "libcosmic", "a", "v0", "pkg_1", "x-y-z"],
)
def test_valid_names(name: str) -> None:
    doc = component_table(name=name)
    assert parse_manifest(doc).name == name


@pytest.mark.parametrize(
    "name",
    ["-bad", "Bad", "has space", "has/slash", "123bad", "emoji_🚀"],
)
def test_invalid_names_rejected(name: str) -> None:
    doc = component_table(name=name)
    with pytest.raises(ManifestError, match="must match"):
        parse_manifest(doc)


@pytest.mark.parametrize(
    "url",
    ["not-a-url", "https://", "git@host", "ftp://example.com/x"],
)
def test_invalid_git_urls_rejected(url: str) -> None:
    doc = component_table(git_url=url)
    with pytest.raises(ManifestError, match="is not a git URL"):
        parse_manifest(doc)


def test_floating_tag_rejected() -> None:
    doc = component_table(tag="main")
    with pytest.raises(ManifestError, match="not a pinned reference"):
        parse_manifest(doc)


def test_duplicate_dependency_name_rejected() -> None:
    doc = component_table()
    dep = dependency_table(name="winit")
    doc["deps"] = [dep, dict(dep)]
    with pytest.raises(ManifestError, match="duplicate dependency name 'winit'"):
        parse_manifest(doc)


def test_deps_wrong_type_raises() -> None:
    doc = component_table()
    doc["deps"] = "winit"  # type: ignore[assignment]
    with pytest.raises(ManifestError, match="expected an array of tables"):
        parse_manifest(doc)


# --------------------------------------------------------------------------- #
# Loader — read a real file from disk
# --------------------------------------------------------------------------- #


def test_load_manifest_reads_committed_example() -> None:
    m = load_manifest(ROOT / "examples" / "cosmic-text.toml")
    assert m.name == "cosmic-text"
    assert m.version == "v0.6.0"
    assert m.license == "MIT OR Apache-2.0"
    assert m.features == ("default", "render")
    assert m.binaries == ("cosmic-text",)
    assert {d.name for d in m.deps} == {"smithay-clipboard", "winit"}


def test_load_manifest_roundtrips_through_tomllib() -> None:
    # The committed example must parse back through the stdlib reader.
    with (ROOT / "examples" / "cosmic-text.toml").open("rb") as fh:
        doc = tomllib.load(fh)
    m = parse_manifest(doc, source="cosmic-text.toml")
    assert m.name == "cosmic-text"


def test_load_manifest_file_not_found() -> None:
    with pytest.raises(ManifestError, match="manifest file not found"):
        load_manifest(ROOT / "does-not-exist.toml")


def test_load_manifest_malformed_toml() -> None:
    bad = ROOT / "broken.toml"
    bad.write_text("name = \n", encoding="utf-8")
    try:
        with pytest.raises(ManifestError):
            load_manifest(bad)
    finally:
        bad.unlink()


# --------------------------------------------------------------------------- #
# Data model invariants
# --------------------------------------------------------------------------- #


def test_manifest_is_frozen_immutable() -> None:
    m = parse_manifest(component_table())
    with pytest.raises(Exception):  # noqa: PT011 — frozen dataclass raises
        m.name = "other"  # type: ignore[misc]


def test_dependency_is_frozen_immutable() -> None:
    dep = dependency_table()
    with pytest.raises(Exception):  # noqa: PT011
        dep.name = "other"  # type: ignore[misc]


def test_component_table_helper_defaults() -> None:
    table = component_table()
    assert table["name"] == "cosmic-text"
    assert table["tag"] == "v0.6.0"
    assert "features" not in table  # omitted when None
    assert "binaries" not in table
