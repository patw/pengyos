"""Tests for the pins manifest loader.

Covers :func:`load_pins`, :class:`PinsManifest`, and :class:`PinsError`: the
manifest shape (a ``component`` array of tables), validation errors, and the
component value objects produced.

Standard library only; no I/O beyond reading the temp-file manifests.
"""

from __future__ import annotations

import pytest

from pengyos_upstream_watch.fetcher import Component
from pengyos_upstream_watch.pins import PinsError, load_pins


def _write(tmp_path, text: str):
    pins = tmp_path / "pins.toml"
    pins.write_text(text, encoding="utf-8")
    return pins


# --- happy path -------------------------------------------------------------


def test_load_pins_reads_component_array(tmp_path):
    pins = _write(
        tmp_path,
        '[[component]]\nname = "busybox"\nversion = "1.36.1"\n'
        '[[component]]\nname = "glibc"\nversion = "2.39"\nurl = "https://ftp.gnu.org/glibc/"\n',
    )
    manifest = load_pins(pins)
    assert len(manifest) == 2
    assert manifest.components == [
        Component("busybox", "1.36.1", None),
        Component("glibc", "2.39", "https://ftp.gnu.org/glibc/"),
    ]


def test_load_pins_empty_manifest(tmp_path):
    # No `component` key at all -> no pins, not an error.
    pins = _write(tmp_path, '# nothing here\n')
    manifest = load_pins(pins)
    assert manifest.components == []
    assert len(manifest) == 0


def test_load_pins_empty_component_array(tmp_path):
    pins = _write(tmp_path, 'component = []\n')
    assert load_pins(pins).components == []


def test_load_pins_url_optional(tmp_path):
    pins = _write(tmp_path, '[[component]]\nname = "busybox"\nversion = "1.36.1"\n')
    (component,) = load_pins(pins).components
    assert component.url is None


def test_load_pins_preserves_order(tmp_path):
    pins = _write(
        tmp_path,
        '[[component]]\nname = "b"\nversion = "1"\n'
        '[[component]]\nname = "a"\nversion = "1"\n',
    )
    order = [c.name for c in load_pins(pins).components]
    assert order == ["b", "a"]


# --- validation errors ------------------------------------------------------


def test_load_pins_missing_file_raises(tmp_path):
    with pytest.raises(PinsError):
        load_pins(tmp_path / "does-not-exist.toml")


def test_load_pins_invalid_toml_raises(tmp_path):
    pins = _write(tmp_path, "this is = = not valid toml [[[\n")
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_top_level_not_table_raises(tmp_path):
    pins = _write(tmp_path, 'component = "busybox"\n')
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_component_not_array_raises(tmp_path):
    # A single table, not an array of tables.
    pins = _write(tmp_path, 'component = { name = "busybox", version = "1.36.1" }\n')
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_missing_name_raises(tmp_path):
    pins = _write(tmp_path, '[[component]]\nversion = "1.36.1"\n')
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_missing_version_raises(tmp_path):
    pins = _write(tmp_path, '[[component]]\nname = "busybox"\n')
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_non_string_version_raises(tmp_path):
    pins = _write(tmp_path, '[[component]]\nname = "busybox"\nversion = 1.36\n')
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_empty_name_raises(tmp_path):
    pins = _write(tmp_path, '[[component]]\nname = "   "\nversion = "1.36"\n')
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_entry_not_table_raises(tmp_path):
    pins = _write(tmp_path, 'component = ["busybox"]\n')
    with pytest.raises(PinsError):
        load_pins(pins)


def test_load_pins_duplicate_names_last_wins(tmp_path):
    pins = _write(
        tmp_path,
        '[[component]]\nname = "busybox"\nversion = "1.36.1"\n'
        '[[component]]\nname = "busybox"\nversion = "1.37.0"\n',
    )
    (component,) = load_pins(pins).components
    assert component.version == "1.37.0"


# --- PinsError is a ValueError ----------------------------------------------


def test_pins_error_is_value_error():
    assert issubclass(PinsError, ValueError)
