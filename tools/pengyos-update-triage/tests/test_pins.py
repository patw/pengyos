"""Tests for the pins manifest schema + loader (``pengyos_update_triage.pins``)."""

from pathlib import Path

import pytest

from pengyos_update_triage.errors import (
    PinsError,
    PinsFormatError,
    PinsSchemaError,
)
from pengyos_update_triage.pins import PinsManifest, load_pins, parse_pins

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PINS = FIXTURES / "pins"


def pin(name: str) -> Path:
    return PINS / name


# --- valid fixtures ---------------------------------------------------------


def test_load_pins_ok_full_fields():
    manifest = load_pins(pin("pins_ok.toml"))
    assert isinstance(manifest, PinsManifest)
    names = [p.name for p in manifest.pins]
    assert names == ["glibc", "openssl", "curl", "zlib", "python", "make"]

    glibc = manifest.pins[0]
    assert glibc.version == "2.39"
    assert glibc.ecosystem == "alpine"
    assert glibc.aliases == ["libc", "glib"]
    assert glibc.reachability == "core"
    assert glibc.note == "base C library"

    make = manifest.pins[5]
    assert make.reachability == "opt"


def test_load_pins_minimal_applies_defaults():
    manifest = load_pins(pin("pins_minimal.toml"))
    assert len(manifest.pins) == 1
    z = manifest.pins[0]
    assert z.name == "zlib"
    assert z.version == "1.3.1"
    assert z.ecosystem == "generic"
    assert z.aliases == []
    assert z.reachability == "std"
    assert z.note == ""


def test_load_pins_empty_is_legal():
    manifest = load_pins(pin("pins_empty.toml"))
    assert len(manifest) == 0
    assert list(manifest) == []


def test_find_by_name_and_alias():
    manifest = load_pins(pin("pins_ok.toml"))
    assert [p.name for p in manifest.find("glibc")] == ["glibc"]
    # 'libc' is an alias of glibc.
    assert [p.name for p in manifest.find("libc")] == ["glibc"]
    assert manifest.find("not-a-component") == []


def test_source_line_attached_to_first_component():
    manifest = load_pins(pin("pins_minimal.toml"))
    # pins_minimal.toml: line 1 comment, line 2 comment, line 3 blank,
    # line 4 the [[component]] header.
    assert manifest.pins[0].line == 4


def test_manifest_source_is_recorded():
    manifest = load_pins(pin("pins_ok.toml"))
    assert manifest.source == str(pin("pins_ok.toml"))


# --- parse_pins (pure) ------------------------------------------------------


def test_parse_pins_from_dict():
    pins = parse_pins(
        {
            "schema": "pengyos.pins/v1",
            "component": [
                {"name": "a", "version": "1.0", "reachability": "core"},
                {"name": "b", "version": "2.0"},
            ],
        }
    )
    assert [p.name for p in pins] == ["a", "b"]
    assert pins[0].reachability == "core"
    assert pins[1].reachability == "std"


def test_parse_pins_toplevel_must_be_table():
    with pytest.raises(PinsSchemaError):
        parse_pins(["component", "list", "not", "a", "table"])


def test_parse_pins_component_must_be_list():
    with pytest.raises(PinsSchemaError):
        parse_pins({"component": "glibc"})


# --- schema violations ------------------------------------------------------


def test_missing_name():
    with pytest.raises(PinsSchemaError) as exc:
        load_pins(pin("pins_bad_missing_name.toml"))
    assert "name" in str(exc.value)


def test_missing_version():
    with pytest.raises(PinsSchemaError):
        load_pins(pin("pins_bad_missing_version.toml"))


def test_empty_name():
    with pytest.raises(PinsSchemaError):
        load_pins(pin("pins_bad_empty_name.toml"))


def test_bad_reachability():
    with pytest.raises(PinsSchemaError) as exc:
        load_pins(pin("pins_bad_reachability.toml"))
    assert "reachability" in str(exc.value)


def test_alias_equal_to_name():
    with pytest.raises(PinsSchemaError):
        load_pins(pin("pins_bad_alias_eq_name.toml"))


def test_version_wrong_type():
    with pytest.raises(PinsSchemaError):
        load_pins(pin("pins_bad_version_number.toml"))


def test_alias_wrong_type():
    with pytest.raises(PinsSchemaError):
        load_pins(pin("pins_bad_alias_string.toml"))


def test_duplicate_name():
    with pytest.raises(PinsSchemaError) as exc:
        load_pins(pin("pins_bad_duplicate.toml"))
    assert "duplicate" in str(exc.value)


# --- format violations ------------------------------------------------------


def test_not_toml_is_format_error():
    with pytest.raises(PinsFormatError):
        load_pins(pin("pins_not_toml.txt"))


def test_missing_file_is_pins_error(tmp_path):
    with pytest.raises(PinsError) as exc:
        load_pins(tmp_path / "does-not-exist.toml")
    assert "not found" in str(exc.value)
    assert exc.value.path is not None
