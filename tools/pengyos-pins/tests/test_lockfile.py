"""Tests for the lockfile layer (``pengyos_pins.lockfile``) and the stdlib
TOML writer in ``pengyos_pins.schema.serialize_pins``.

Covers:

* **serialisation** — ``serialize_pins`` produces a deterministic, parseable
  ``pins.lock.toml`` document (name-sorted, edition emitted only when set,
  escaping round-trips);
* **load/write** — ``load_lockfile`` reads a lockfile back into a valid
  :class:`Pins`, and ``write_lockfile`` round-trips through ``load_lockfile``;
* **drift** — ``drift`` detects added / removed / version-changed / hash-changed
  entries (spec.md: "drift detects added/removed/version-changed/hash-changed
  entries"), reports each component once, and orders deterministically;
  ``format_drift`` renders the counts + one line per entry.

All tests are hermetic: no network, no root, files only via ``tmp_path`` or
in-memory documents.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from pengyos_pins.lockfile import (
    ALL_DRIFT_CATEGORIES,
    DRIFT_ADDED,
    DRIFT_HASH_CHANGED,
    DRIFT_REMOVED,
    DRIFT_VERSION_CHANGED,
    drift,
    drift_counts,
    format_drift,
    load_lockfile,
    lockfile_table,
    serialize_lockfile,
    serialize_pins,
    write_lockfile,
)
from pengyos_pins.schema import Pins, PinsError, component_table, parse_pins

# --------------------------------------------------------------------------- #
# Serialisation (schema.serialize_pins / lockfile.serialize_lockfile)
# --------------------------------------------------------------------------- #


class TestSerialize:
    def test_is_parseable_toml(self):
        pins = parse_pins(
            {"component": [component_table("linux"), component_table("busybox")]}
        )
        doc = serialize_pins(pins)
        # Round-trips through the stdlib TOML reader.
        parsed = tomllib.loads(doc)
        assert set(parsed) == {"component"}
        assert len(parsed["component"]) == 2

    def test_name_sorted(self):
        pins = parse_pins(
            {"component": [component_table("zebra"), component_table("alpha")]}
        )
        doc = serialize_pins(pins)
        order = [
            line.split(" = ", 1)[1].strip('"')
            for line in doc.splitlines()
            if line.startswith("name = ")
        ]
        assert order == ["alpha", "zebra"]

    def test_deterministic(self):
        pins = parse_pins(
            {"component": [component_table("linux"), component_table("busybox")]}
        )
        assert serialize_pins(pins) == serialize_pins(pins)

    def test_required_fields_present(self):
        # A component with an edition carries all six required fields plus
        # edition-membership; one without carries exactly the six required.
        with_edition = tomllib.loads(serialize_pins(_one("linux")))["component"][0]
        without_edition = tomllib.loads(serialize_pins(_one("linux", edition=None)))[
            "component"
        ][0]
        required = {
            "name",
            "upstream",
            "version",
            "url",
            "sha256",
            "license",
        }
        assert required <= set(with_edition)
        assert "edition-membership" in with_edition
        assert set(without_edition) == required

    def test_edition_emitted_only_when_set(self):
        with_edition = tomllib.loads(
            serialize_pins(_one("linux", edition="core"))
        )["component"][0]
        without_edition = tomllib.loads(
            serialize_pins(_one("linux", edition=None))
        )["component"][0]
        assert "edition-membership" in with_edition
        assert "edition-membership" not in without_edition

    def test_serialize_lockfile_alias(self):
        # serialize_lockfile is the lockfile layer's alias for serialize_pins.
        assert serialize_lockfile(_one("linux")) == serialize_pins(_one("linux"))

    def test_escaping_round_trips(self):
        # A value with a quote, backslash and newline survives a serialise ->
        # parse round trip unchanged (the writer must escape, the reader unescape).
        pins = parse_pins(
            {
                "component": [
                    component_table(
                        "weird",
                        url='a"b\\c\nd\te',
                        license="weird+value",
                    )
                ]
            }
        )
        parsed = tomllib.loads(serialize_pins(pins))
        assert parsed["component"][0]["url"] == 'a"b\\c\nd\te'

    def test_non_ascii_round_trips(self):
        pins = parse_pins(
            {"component": [component_table("emoji", upstream="https://exämple.org")]}
        )
        parsed = tomllib.loads(serialize_pins(pins))
        assert parsed["component"][0]["upstream"] == "https://exämple.org"


# --------------------------------------------------------------------------- #
# Load / write round trip
# --------------------------------------------------------------------------- #


class TestLoadWrite:
    def test_load_lockfile_returns_valid_pins(self, tmp_path: Path):
        pins = _one("linux")
        path = tmp_path / "pins.lock.toml"
        write_lockfile(pins, path)
        loaded = load_lockfile(path)
        assert isinstance(loaded, Pins)
        assert len(loaded) == 1
        (comp,) = loaded.components
        assert comp.name == "linux"
        assert comp.version == "7.2.5"
        assert comp.edition == "core"

    def test_write_then_load_round_trips_data(self, tmp_path: Path):
        original = _two()
        path = tmp_path / "pins.lock.toml"
        write_lockfile(original, path)
        loaded = load_lockfile(path)
        assert {c.name for c in loaded.components} == {"linux", "busybox"}
        by_name = {c.name: c for c in loaded.components}
        assert by_name["linux"].sha256 == "0" * 64
        assert by_name["busybox"].version == "1.36.1"

    def test_write_returns_path_string(self, tmp_path: Path):
        path = tmp_path / "pins.lock.toml"
        result = write_lockfile(_one("linux"), path)
        assert result == str(path)
        assert path.is_file()

    def test_load_missing_lockfile_raises(self, tmp_path: Path):
        with pytest.raises(PinsError):
            load_lockfile(tmp_path / "does-not-exist.lock.toml")

    def test_load_malformed_lockfile_raises(self, tmp_path: Path):
        # A malformed lockfile raises TOMLDecodeError (an invalid lockfile is a
        # hard error, never a silent drift) — same contract as load_pins.
        bad = tmp_path / "bad.lock.toml"
        bad.write_text("component = [[ not valid")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_lockfile(bad)

    def test_lockfile_is_itself_valid(self, tmp_path: Path):
        # A written lockfile must pass the whole validate contract, not just
        # parse — it is the resolved baseline.
        from pengyos_pins.validate import validate_pins

        pins = _two()
        path = tmp_path / "pins.lock.toml"
        write_lockfile(pins, path)
        assert validate_pins(load_lockfile(path)) == []


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #


class TestDrift:
    def test_no_drift_when_equal(self):
        pins = _two()
        assert drift(pins, pins) == []

    def test_added_entry(self):
        locked = _one("linux")
        working = _two()  # adds busybox
        entries = drift(working, locked)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.category == DRIFT_ADDED
        assert entry.name == "busybox"
        assert entry.working_version == "1.36.1"
        assert entry.locked_version == ""

    def test_removed_entry(self):
        locked = _two()  # linux + busybox
        working = _one("linux")  # only linux
        entries = drift(working, locked)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.category == DRIFT_REMOVED
        assert entry.name == "busybox"
        assert entry.locked_version == "1.36.1"
        assert entry.working_version == ""

    def test_version_changed_entry(self):
        locked = parse_pins(
            {"component": [component_table("linux", version="7.2.5")]}
        )
        working = parse_pins(
            {"component": [component_table("linux", version="7.3.0")]}
        )
        entries = drift(working, locked)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.category == DRIFT_VERSION_CHANGED
        assert entry.locked_version == "7.2.5"
        assert entry.working_version == "7.3.0"

    def test_hash_changed_entry(self):
        locked = parse_pins(
            {"component": [component_table("linux", sha256="a" * 64)]}
        )
        working = parse_pins(
            {"component": [component_table("linux", sha256="b" * 64)]}
        )
        entries = drift(working, locked)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.category == DRIFT_HASH_CHANGED
        assert entry.locked_sha256 == "a" * 64
        assert entry.working_sha256 == "b" * 64

    def test_version_change_takes_precedence_over_hash(self):
        # A component that changed both version and hash is reported once as a
        # version change, not also as a hash change.
        locked = parse_pins(
            {"component": [component_table("linux", version="7.2.5", sha256="a" * 64)]}
        )
        working = parse_pins(
            {"component": [component_table("linux", version="7.3.0", sha256="b" * 64)]}
        )
        entries = drift(working, locked)
        assert [e.category for e in entries] == [DRIFT_VERSION_CHANGED]

    def test_detects_all_four_categories(self):
        locked = parse_pins(
            {
                "component": [
                    component_table("linux", version="7.2.5", sha256="a" * 64),
                    component_table("busybox", version="1.36.1", sha256="b" * 64),
                    component_table("gcc", version="14.0", sha256="c" * 64),
                ]
            }
        )
        working = parse_pins(
            {
                "component": [
                    component_table("linux", version="7.3.0", sha256="a" * 64),
                    component_table("busybox", version="1.36.1", sha256="d" * 64),
                    component_table("musl", version="1.2.5", sha256="e" * 64),
                ]
            }
        )
        entries = drift(working, locked)
        categories = [e.category for e in entries]
        assert DRIFT_ADDED in categories  # musl
        assert DRIFT_REMOVED in categories  # gcc
        assert DRIFT_VERSION_CHANGED in categories  # linux
        assert DRIFT_HASH_CHANGED in categories  # busybox
        # Each component appears at most once.
        assert len(entries) == 4

    def test_deterministic_order(self):
        locked = parse_pins(
            {
                "component": [
                    component_table("linux", version="7.2.5"),
                    component_table("busybox", version="1.36.1", sha256="b" * 64),
                    component_table("gcc", version="14.0"),
                    component_table("coreutils", version="9.0"),
                ]
            }
        )
        working = parse_pins(
            {
                "component": [
                    component_table("linux", version="7.3.0"),
                    component_table("busybox", version="1.36.1", sha256="d" * 64),
                    component_table("musl", version="1.2.5"),
                    component_table("apt", version="2.0"),
                ]
            }
        )
        first = [
            (e.category, e.name) for e in drift(working, locked)
        ]
        second = [
            (e.category, e.name) for e in drift(working, locked)
        ]
        assert first == second

    def test_order_is_added_then_removed_then_changes(self):
        locked = parse_pins(
            {"component": [component_table("linux"), component_table("gcc")]}
        )
        working = parse_pins(
            {"component": [component_table("linux"), component_table("musl")]}
        )
        order = [(e.category, e.name) for e in drift(working, locked)]
        # gcc removed, musl added, linux unchanged -> added & removed only.
        assert order == [(DRIFT_ADDED, "musl"), (DRIFT_REMOVED, "gcc")]

    def test_empty_both_sides(self):
        # Construct empty Pins directly (parse_pins rejects empty arrays).
        assert drift(Pins(components=()), Pins(components=())) == []


class TestDriftCounts:
    def test_counts_match_entries(self):
        locked = _one("linux")
        working = _two()
        entries = drift(working, locked)
        counts = drift_counts(entries)
        assert counts[DRIFT_ADDED] == 1
        assert counts[DRIFT_REMOVED] == 0
        assert counts[DRIFT_VERSION_CHANGED] == 0
        assert counts[DRIFT_HASH_CHANGED] == 0
        assert set(counts) == set(ALL_DRIFT_CATEGORIES)


class TestFormatDrift:
    def test_no_drift_message(self):
        text = format_drift([])
        assert "no drift" in text

    def test_reports_counts_and_lines(self):
        locked = parse_pins(
            {"component": [component_table("linux", version="7.2.5")]}
        )
        working = parse_pins(
            {"component": [component_table("linux", version="7.3.0")]}
        )
        text = format_drift(drift(working, locked))
        assert "drift detected" in text
        assert "version:" in text
        assert "7.2.5 -> 7.3.0" in text

    def test_added_and_removed_lines(self):
        locked = _one("linux")
        working = _two()
        text = format_drift(drift(working, locked))
        assert "added:" in text
        assert "busybox" in text

    def test_hash_line_shares_prefix(self):
        locked = parse_pins(
            {"component": [component_table("linux", sha256="a" * 64)]}
        )
        working = parse_pins(
            {"component": [component_table("linux", sha256="b" * 64)]}
        )
        text = format_drift(drift(working, locked))
        assert "hash:" in text
        assert "aaaaaaaaaa" in text  # first 12 chars of the locked digest
        assert "bbbbbbbbbb" in text


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _one(name: str = "linux", *, edition: str | None = "core") -> Pins:
    return parse_pins(
        {"component": [component_table(name, edition_membership=edition)]}
    )


def _two() -> Pins:
    # A baseline (_one("linux")) plus a second component; the linux entry is
    # byte-identical to _one("linux") so drift sees only the busybox addition.
    return parse_pins(
        {
            "component": [
                component_table("linux", edition_membership="core"),
                component_table("busybox", version="1.36.1", sha256="b" * 64),
            ]
        }
    )
