"""Pins manifest input schema (TOML) and loader.

The pins manifest declares the ``(name, version)`` pairs that make a PengyOS
build reproducible. It is TOML so it composes with the rest of the distro's
build metadata.

Schema
------
A pins manifest is a TOML document whose components live in an array of tables
named ``component``::

    schema = "pengyos.pins/v1"      # optional, informational

    [[component]]
    name        = "glibc"           # REQUIRED — non-empty string
    version     = "2.39"            # REQUIRED — non-empty string
    ecosystem   = "alpine"          # optional, default "generic"
    aliases     = ["libc"]          # optional, list of strings
    reachability = "core"           # optional, one of opt/std/core, default "std"
    note        = "base C library"  # optional, free text

Top-level keys other than ``component`` (e.g. ``schema``, ``description``) are
ignored — the manifest is expected to be a *subset* of a larger build file, so
we are deliberately lenient about siblings and strict only about each
``[[component]]`` table.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import List, Union

from .errors import PinsError, PinsFormatError, PinsSchemaError
from .models import Pin, REACHABILITY_LEVELS, DEFAULT_REACHABILITY

# The pins schema is TOML, and stdlib TOML parsing (``tomllib``) requires
# Python 3.11+. This is a documented dependency of the tool, not a
# third-party one.
_MIN_TOML_PYTHON = (3, 11)

__all__ = ["load_pins", "parse_pins", "PinsManifest"]


class PinsManifest:
    """The loaded pins manifest: an ordered list of :class:`Pin`."""

    def __init__(self, pins: List[Pin], source: Union[str, Path, None] = None) -> None:
        self.pins = pins
        self.source = str(source) if source is not None else None

    def __iter__(self):
        return iter(self.pins)

    def __len__(self) -> int:
        return len(self.pins)

    def find(self, name: str) -> List[Pin]:
        """All pins whose primary name or any alias equals ``name``."""
        return [p for p in self.pins if p.matches_name(name)]

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PinsManifest(pins={self.pins!r}, source={self.source!r})"


def parse_pins(document: object) -> List[Pin]:
    """Parse an already-decoded TOML document (``dict``) into a list of :class:`Pin`.

    Separated from :func:`load_pins` so the schema rules are unit-testable
    without touching the filesystem. Raises :class:`PinsSchemaError` on any
    structural violation.
    """
    if not isinstance(document, dict):
        raise PinsSchemaError(
            "pins manifest must be a TOML table (the top level must be a table)"
        )

    raw_components = document.get("component")
    if raw_components is None:
        # An empty manifest is legal: "no pinned components" is a valid state
        # (a run over it yields zero proposals, which is a success).
        return []
    if not isinstance(raw_components, list):
        raise PinsSchemaError(
            "'component' must be an array of tables ([[component]]); "
            f"got {type(raw_components).__name__}"
        )

    pins: List[Pin] = []
    seen = set()
    for idx, table in enumerate(raw_components):
        # We record the 1-based *table index* as the line hint; the true source
        # line is not recoverable from a decoded dict, so line stays None here
        # and load_pins (which reads raw text) can enrich it.
        pin = _parse_component(table, index=idx, seen=seen)
        pins.append(pin)
    return pins


def _parse_component(table: object, index: int, seen: set) -> Pin:
    where = f"component #{index + 1}"
    if not isinstance(table, dict):
        raise PinsSchemaError(
            f"{where}: each [[component]] must be a TOML table, got "
            f"{type(table).__name__}"
        )

    name = _require_str(table, "name", where, allow_empty=False)
    version = _require_str(table, "version", where, allow_empty=False)

    if name in seen:
        raise PinsSchemaError(
            f"{where}: duplicate component name {name!r}; each component name "
            "must be unique in the manifest"
        )
    seen.add(name)

    ecosystem = _optional_str(table, "ecosystem", where) or "generic"
    aliases = _str_list(table, "aliases", where, default=[])
    reachability = _optional_str(table, "reachability", where) or DEFAULT_REACHABILITY
    if reachability not in REACHABILITY_LEVELS:
        raise PinsSchemaError(
            f"{where}: reachability {reachability!r} must be one of "
            f"{', '.join(REACHABILITY_LEVELS)}"
        )
    note = _optional_str(table, "note", where) or ""

    # Guard against an alias equal to the primary name or a duplicate alias.
    for alias in aliases:
        if alias == name:
            raise PinsSchemaError(
                f"{where}: alias {alias!r} equals the component name; "
                "aliases must differ from 'name'"
            )

    return Pin(
        name=name,
        version=version,
        ecosystem=ecosystem,
        aliases=aliases,
        reachability=reachability,
        note=note,
    )


def _require_str(table: dict, key: str, where: str, allow_empty: bool = True) -> str:
    value = table.get(key)
    if value is None:
        raise PinsSchemaError(f"{where}: missing required string field {key!r}")
    if not isinstance(value, str):
        raise PinsSchemaError(
            f"{where}: field {key!r} must be a string, got {type(value).__name__}"
        )
    if not allow_empty and not value.strip():
        raise PinsSchemaError(f"{where}: field {key!r} must be a non-empty string")
    return value


def _optional_str(table: dict, key: str, where: str) -> "str | None":
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, str):
        raise PinsSchemaError(
            f"{where}: field {key!r} must be a string, got {type(value).__name__}"
        )
    return value


def _str_list(
    table: dict, key: str, where: str, default: List[str]
) -> List[str]:
    if key not in table:
        return list(default)
    value = table[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PinsSchemaError(
            f"{where}: field {key!r} must be an array of strings, got "
            f"{type(value).__name__}"
        )
    return list(value)


def load_pins(source: Union[str, Path]) -> PinsManifest:
    """Read and parse the pins manifest at ``source`` into a :class:`PinsManifest`.

    Raises
    ------
    PinsFormatError
        if the file cannot be read or is not valid TOML.
    PinsSchemaError
        if the TOML is valid but the structure violates the documented schema.
    """
    path = Path(source)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise PinsError(f"pins manifest not found: {path}", path=str(path)) from exc
    except OSError as exc:
        raise PinsError(f"could not read pins manifest {path}: {exc}", path=str(path)) from exc

    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise PinsFormatError(f"invalid TOML in pins manifest: {exc}", path=str(path)) from exc

    pins = parse_pins(document)
    _attach_source_lines(pins, raw.decode("utf-8"), path)
    return PinsManifest(pins=pins, source=str(path))


def _attach_source_lines(pins: List[Pin], text: str, path: Path) -> None:
    """Best-effort: record the line number of each ``[[component]]`` table.

    ``tomllib`` does not retain source positions, so we locate the Nth
    ``[[component]]`` header (in order) and record its 1-based line. If a
    header cannot be matched, the pin's ``line`` is left as ``None`` — this is a
    diagnostic nicety, never a failure.
    """
    lines = text.splitlines()
    header_lines: List[int] = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "[[component]]":
            header_lines.append(lineno)
    for i, pin in enumerate(pins):
        if i < len(header_lines):
            # dataclasses are frozen; rebuild with the enriched line.
            pins[i] = Pin(
                name=pin.name,
                version=pin.version,
                ecosystem=pin.ecosystem,
                aliases=pin.aliases,
                reachability=pin.reachability,
                note=pin.note,
                line=header_lines[i],
            )
    # Pin list is stable; nothing else to do. ``path`` retained for diagnostics.
