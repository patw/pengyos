"""Tests for the ``pins.toml`` schema (pengyos_pins.schema).

Covers the data model, the pure loader, and every structural/schema-level
validation rule.  These tests are hermetic: no network, no root, no files on
disk except through ``tmp_path`` fixtures.
"""

from __future__ import annotations

import tomllib
import textwrap
from pathlib import Path

import pytest

from pengyos_pins.schema import (
    COMPONENTS_KEY,
    OPTIONAL_FIELDS,
    REQUIRED_FIELDS,
    Component,
    Pins,
    PinsError,
    component_table,
    get_component,
    load_pins,
    parse_pins,
)

ROOT = Path(__file__).resolve().parents[1]


def _two_component_doc() -> dict:
    """A minimal, valid two-component document."""
    return {
        COMPONENTS_KEY: [
            component_table("linux", edition_membership="core"),
            component_table("busybox", edition_membership="core"),
        ]
    }


class TestModel:
    def test_required_and_optional_field_lists(self):
        assert REQUIRED_FIELDS == (
            "name",
            "upstream",
            "version",
            "url",
            "sha256",
            "license",
        )
        assert OPTIONAL_FIELDS == {"edition-membership": ""}

    def test_parse_returns_pins_of_components(self):
        pins = parse_pins(_two_component_doc())
        assert isinstance(pins, Pins)
        assert all(isinstance(c, Component) for c in pins.components)
        assert len(pins) == 2

    def test_fields_populated(self):
        (linux,) = [c for c in parse_pins(_two_component_doc()).components
                    if c.name == "linux"]
        assert linux.upstream == "https://linux.org"
        assert linux.version == "7.2.5"
        assert linux.url == "https://cdn.org/linux-7.2.5.tar.xz"
        assert linux.sha256 == "0" * 64
        assert linux.license == "GPL-2.0-only"

    def test_component_is_immutable(self):
        (linux,) = [c for c in parse_pins(_two_component_doc()).components
                    if c.name == "linux"]
        with pytest.raises(Exception):
            linux.version = "9.9"  # type: ignore[misc]

    def test_edition_defaults_to_empty_and_has_edition_false(self):
        pins = parse_pins({COMPONENTS_KEY: [component_table("busybox")]})
        assert pins.components[0].edition == ""
        assert pins.components[0].has_edition is False

    def test_edition_membership_populates_edition(self):
        (linux,) = [c for c in parse_pins(_two_component_doc()).components
                    if c.name == "linux"]
        assert linux.edition == "core"
        assert linux.has_edition is True

    def test_edition_names_sorted_and_deduped(self):
        doc = {
            COMPONENTS_KEY: [
                component_table("a", edition_membership="server"),
                component_table("b", edition_membership="core"),
                component_table("c"),  # unassigned
                component_table("d", edition_membership="server"),
            ]
        }
        assert parse_pins(doc).edition_names == ["core", "server"]

    def test_component_table_omits_edition_when_none(self):
        table = component_table("x")
        assert "edition-membership" not in table
        # and it still parses as unassigned
        assert parse_pins({COMPONENTS_KEY: [table]}).components[0].edition == ""

    def test_component_table_allows_edition_override(self):
        table = component_table("x", edition_membership="desktop")
        assert table["edition-membership"] == "desktop"


class TestStructuralErrors:
    def test_top_level_not_a_table(self):
        with pytest.raises(PinsError, match="top level must be a table"):
            parse_pins([COMPONENTS_KEY])

    def test_missing_component_key(self):
        with pytest.raises(PinsError, match="missing top-level 'component'"):
            parse_pins({"other": []})

    def test_component_not_an_array(self):
        with pytest.raises(PinsError, match="'component' must be an array"):
            parse_pins({COMPONENTS_KEY: {}})

    def test_component_empty(self):
        with pytest.raises(PinsError, match="must not be empty"):
            parse_pins({COMPONENTS_KEY: []})

    def test_entry_not_a_table(self):
        with pytest.raises(PinsError, match=r"component\[0\]: expected a table"):
            parse_pins({COMPONENTS_KEY: ["nope"]})

    def test_error_carries_source_name(self):
        with pytest.raises(PinsError, match="my-pins.toml"):
            parse_pins({"nope": 1}, source="my-pins.toml")


class TestFieldErrors:
    @pytest.mark.parametrize(
        "field", ["name", "upstream", "version", "url", "sha256", "license"]
    )
    def test_missing_required_field(self, field):
        doc = _two_component_doc()
        del doc[COMPONENTS_KEY][1][field]
        with pytest.raises(
            PinsError,
            match=f"component\\[1\\]: missing required field '{field}'",
        ):
            parse_pins(doc)

    def test_non_string_field(self):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][0]["version"] = 2026
        with pytest.raises(PinsError, match=r"expected a string, got int"):
            parse_pins(doc)

    def test_empty_string_field(self):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][0]["name"] = "   "
        with pytest.raises(
            PinsError, match=r"component\[0\].name: must not be empty"
        ):
            parse_pins(doc)

    def test_unknown_field(self):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][0]["extra"] = 1
        with pytest.raises(PinsError, match=r"component\[0\]: unknown field\(s\): extra"):
            parse_pins(doc)

    def test_unknown_fields_sorted_and_joined(self):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][0].update({"zeta": "1", "alpha": "2"})
        with pytest.raises(
            PinsError, match=r"unknown field\(s\): alpha, zeta"
        ):
            parse_pins(doc)

    def test_non_string_optional_field(self):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][0]["edition-membership"] = 3
        with pytest.raises(
            PinsError, match=r"edition-membership: expected a string, got int"
        ):
            parse_pins(doc)


class TestValueErrors:
    @pytest.mark.parametrize(
        "name",
        ["Bad", "has space", "dot.name", "with/slash", "_lead", "-lead"],
    )
    def test_invalid_name(self, name):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][0]["name"] = name
        with pytest.raises(PinsError, match=r"must match \[a-z0-9\]"):
            parse_pins(doc)

    def test_valid_name_with_digits_underscore_dash(self):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][0]["name"] = "1ok_ok-2"
        assert parse_pins(doc).components[0].name == "1ok_ok-2"

    def test_duplicate_name(self):
        doc = _two_component_doc()
        doc[COMPONENTS_KEY][1]["name"] = "linux"
        with pytest.raises(
            PinsError, match=r"duplicate component name 'linux'"
        ):
            parse_pins(doc)


class TestLookup:
    def test_get_component_found(self):
        pins = parse_pins(_two_component_doc())
        assert get_component(pins.components, "busybox").version == "7.2.5"

    def test_get_component_unknown(self):
        pins = parse_pins(_two_component_doc())
        with pytest.raises(
            PinsError,
            match=r"unknown component 'ghost' \(known components: linux, busybox\)",
        ):
            get_component(pins.components, "ghost")

    def test_get_component_empty(self):
        with pytest.raises(
            PinsError, match=r"unknown component 'x' \(known components: <none>\)"
        ):
            get_component([], "x")


class TestLoadFromDisk:
    def test_load_pins_roundtrip(self, tmp_path: Path):
        toml_file = tmp_path / "pins.toml"
        toml_file.write_text(
            textwrap.dedent(
                """\
                [[component]]
                name = "linux"
                upstream = "https://linux.org"
                version = "7.2.5"
                url = "https://cdn.org/linux-7.2.5.tar.xz"
                sha256 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                license = "GPL-2.0-only"
                edition-membership = "core"
                """
            )
        )
        pins = load_pins(toml_file)
        linux = get_component(pins.components, "linux")
        assert linux.version == "7.2.5"
        assert linux.edition == "core"
        assert linux.has_edition is True

    def test_load_pins_missing_file(self, tmp_path: Path):
        with pytest.raises(PinsError, match="pins file not found"):
            load_pins(tmp_path / "nope.toml")

    def test_load_pins_malformed_toml(self, tmp_path: Path):
        bad = tmp_path / "bad.toml"
        bad.write_text("component = [[ not valid")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_pins(bad)


class TestRepoPinsToml:
    """The committed pins.toml must itself parse (once authored)."""

    def test_committed_pins_file_valid(self):
        pins_path = ROOT / "pins.toml"
        if not pins_path.is_file():
            pytest.skip("pins.toml not authored yet")
        pins = load_pins(pins_path)
        assert len(pins) >= 1
