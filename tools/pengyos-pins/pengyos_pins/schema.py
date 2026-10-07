"""The ``pins.toml`` schema — the pinned-component data model.

``pengyos-pins`` is a single source of truth for pinned components.  Each
component records its **version**, **upstream**, **url**, **sha256**,
**license**, and optional **edition-membership**.  This module defines that
schema: the :class:`Component` data model, the :class:`Pins` container, and the
pure, side-effect-free loader that turns a parsed ``pins.toml`` document into a
:class:`Pins`.

A ``pins.toml`` file is a table whose single ``component`` key is an array of
tables, one per pinned component::

    [[component]]
    name = "linux"                       # [a-z0-9][a-z0-9_-]*, unique
    upstream = "https://linux.org"       # canonical upstream URL
    version = "7.2.5"                    # upstream version string
    url = "https://cdn.org/linux-7.2.5.tar.xz"
    sha256 = "0123...abcd"               # 64 lowercase hex chars
    license = "GPL-2.0-only"            # SPDX expression
    edition-membership = "core"         # optional

### Field reference

======================  =========  ==============================================
field                   required   notes
======================  =========  ==============================================
``name``                yes        ``[a-z0-9][a-z0-9_-]*``; unique in the file
``upstream``            yes        canonical upstream URL (non-empty string)
``version``             yes        upstream version string
``url``                 yes        source download URL (non-empty string)
``sha256``              yes        hex digest of the release tarball
``license``             yes        SPDX license expression
``edition-membership``  no         optional edition name (default ``""``)
======================  =========  ==============================================

The ``sha256`` hex-format and the ``license`` SPDX-syntax rules are enforced by
the validation layer (``pengyos_pins.validate``); this module only guarantees
the fields are present, non-empty, correctly typed, and that names are valid
and unique.  Parsing is a pure operation — nothing is required to exist on disk
at parse time — so it is safe to run in hermetic, network-free tests.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

#: Required fields, in declaration order.  Every component table must carry all
#: of these.
REQUIRED_FIELDS: tuple[str, ...] = (
    "name",
    "upstream",
    "version",
    "url",
    "sha256",
    "license",
)
#: Optional fields and their defaults.  ``edition-membership`` is the only
#: optional field; it defaults to the empty string (meaning "unassigned").
OPTIONAL_FIELDS: dict[str, str] = {"edition-membership": ""}

#: The canonical key under which components are declared in ``pins.toml``.
COMPONENTS_KEY = "component"

#: Component names: lowercase alphanumeric, underscores and dashes, starting
#: alphanumeric (mirrors Buildroot package-name hygiene).
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class PinsError(ValueError):
    """A ``pins.toml`` file is missing, malformed, or fails schema validation."""


@dataclass(frozen=True)
class Component:
    """One validated pinned component.

    The six required fields plus an optional ``edition`` (the value of
    ``edition-membership``).  Instances are immutable.
    """

    name: str
    upstream: str
    version: str
    url: str
    sha256: str
    license: str
    edition: str = ""

    @property
    def has_edition(self) -> bool:
        """``True`` when this component is assigned to an edition."""
        return bool(self.edition)


@dataclass(frozen=True)
class Pins:
    """The validated set of pinned components from a ``pins.toml`` document."""

    components: tuple[Component, ...] = field(default_factory=tuple)

    @property
    def edition_names(self) -> list[str]:
        """Sorted, de-duplicated edition names in use (excluding unassigned)."""
        return sorted({c.edition for c in self.components if c.edition})

    def __len__(self) -> int:
        return len(self.components)


def _require_str(component: dict, key: str, where: str) -> str:
    """Return ``component[key]`` as a non-empty string, else raise."""
    value = component[key]
    if not isinstance(value, str):
        raise PinsError(
            f"{where}.{key}: expected a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise PinsError(f"{where}.{key}: must not be empty")
    return value


def _parse_entry(entry: object, index: int, seen: dict[str, int]) -> Component:
    """Validate one ``[[component]]`` table and build a :class:`Component`."""
    where = f"component[{index}]"
    if not isinstance(entry, dict):
        raise PinsError(
            f"{where}: expected a table, got {type(entry).__name__}"
        )

    for key in REQUIRED_FIELDS:
        if key not in entry:
            raise PinsError(f"{where}: missing required field '{key}'")

    unknown = sorted(set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise PinsError(f"{where}: unknown field(s): {', '.join(unknown)}")

    name = _require_str(entry, "name", where)
    if not _NAME_RE.match(name):
        raise PinsError(
            f"{where}.name: '{name}' must match [a-z0-9][a-z0-9_-]* "
            "(lowercase letters, digits, '_' or '-', starting alphanumeric)"
        )
    if name in seen:
        raise PinsError(
            f"component[{seen[name]}]: duplicate component name '{name}'"
        )

    upstream = _require_str(entry, "upstream", where)
    version = _require_str(entry, "version", where)
    url = _require_str(entry, "url", where)
    sha256 = _require_str(entry, "sha256", where)
    license_ = _require_str(entry, "license", where)

    edition_raw = entry.get("edition-membership", OPTIONAL_FIELDS["edition-membership"])
    if not isinstance(edition_raw, str):
        raise PinsError(
            f"{where}.edition-membership: expected a string, "
            f"got {type(edition_raw).__name__}"
        )

    seen[name] = index
    return Component(
        name=name,
        upstream=upstream,
        version=version,
        url=url,
        sha256=sha256,
        license=license_,
        edition=edition_raw,
    )


def parse_pins(
    data: object,
    base_dir: Path | str = ".",
    source: str = "pins.toml",
) -> Pins:
    """Validate a parsed ``pins.toml`` document (``tomllib.load`` output).

    ``base_dir`` is accepted for API symmetry with sibling loaders (no paths are
    resolved here — ``url``/``upstream`` are absolute URLs); ``source`` is the
    file name used in error messages.
    """
    if not isinstance(data, dict):
        raise PinsError(
            f"{source}: top level must be a table, got {type(data).__name__}"
        )
    if COMPONENTS_KEY not in data:
        raise PinsError(f"{source}: missing top-level '{COMPONENTS_KEY}' array")
    entries = data[COMPONENTS_KEY]
    if not isinstance(entries, list):
        raise PinsError(
            f"{source}: '{COMPONENTS_KEY}' must be an array, "
            f"got {type(entries).__name__}"
        )
    if not entries:
        raise PinsError(f"{source}: '{COMPONENTS_KEY}' must not be empty")

    seen: dict[str, int] = {}
    components: list[Component] = []
    for index, entry in enumerate(entries):
        components.append(_parse_entry(entry, index, seen))
    return Pins(components=tuple(components))


def load_pins(path: Path | str) -> Pins:
    """Read and validate ``pins.toml`` at ``path`` (the file must exist)."""
    path = Path(path)
    if not path.is_file():
        raise PinsError(f"pins file not found: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return parse_pins(data, base_dir=path.parent, source=path.name)


def get_component(pins: Sequence[Component], name: str) -> Component:
    """Return the component called ``name`` or raise :class:`PinsError`."""
    for component in pins:
        if component.name == name:
            return component
    known = ", ".join(c.name for c in pins) or "<none>"
    raise PinsError(f"unknown component '{name}' (known components: {known})")


#: The ordered field sequence written to a lockfile for each component.  The
#: optional ``edition-membership`` is emitted only when the component is assigned
#: to an edition (see :func:`serialize_pins`).
LOCKFILE_FIELDS: tuple[str, ...] = (
    "name",
    "upstream",
    "version",
    "url",
    "sha256",
    "license",
)


def _toml_escape(value: str) -> str:
    """Escape a ``str`` for a TOML basic (double-quoted) string.

    Only the characters TOML requires inside a basic string are escaped
    (``\\``, ``"`` and the control-char escapes); every other character —
    including non-ASCII — is passed through, so a plain URL or an emoji round
    trips losslessly through :func:`tomllib.loads`.
    """
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _toml_string(value: str) -> str:
    """Return ``value`` as a TOML basic string literal (with quotes)."""
    return '"' + _toml_escape(value) + '"'


def serialize_pins(
    pins: Pins,
    *,
    header: str | None = None,
) -> str:
    """Serialise a :class:`Pins` to a ``pins.lock.toml`` document.

    The output is a TOML array-of-tables under ``component`` — the same shape
    :func:`load_pins`/:func:`parse_pins` read back — with components in
    name-sorted order so the bytes are deterministic for a given input.  The
    optional ``edition-membership`` field is emitted only when the component is
    assigned to an edition (matching :func:`component_table`).

    This is a tiny stdlib-only TOML writer (``tomllib`` can only *read* TOML),
    sufficient for the flat, all-string lockfile schema.  The result round-trips
    through :func:`tomllib.loads` and :func:`parse_pins`.
    """
    lines = [
        "# pins.lock.toml — resolved component lockfile",
        "# Generated by pengyos-pins. Do not edit by hand — run `pengyos-pins lock`.",
        "",
    ]
    if header:
        lines.append(f"# {header}")
        lines.append("")
    for component in sorted(pins.components, key=lambda c: c.name):
        lines.append("[[component]]")
        for field in LOCKFILE_FIELDS:
            lines.append(f"{field} = {_toml_string(getattr(component, field))}")
        if component.edition:
            lines.append(
                f'edition-membership = {_toml_string(component.edition)}'
            )
        lines.append("")
    return "\n".join(lines)


def component_table(
    name: str = "linux",
    *,
    upstream: str = "https://linux.org",
    version: str = "7.2.5",
    url: str = "https://cdn.org/linux-7.2.5.tar.xz",
    sha256: str = "0" * 64,
    license: str = "GPL-2.0-only",
    edition_membership: str | None = None,
) -> dict:
    """Build a minimal valid ``[[component]]`` table for tests and callers.

    Every field is overridable; omit ``edition_membership`` to leave it out of
    the table entirely (the schema then treats it as unassigned).
    """
    table: dict = {
        "name": name,
        "upstream": upstream,
        "version": version,
        "url": url,
        "sha256": sha256,
        "license": license,
    }
    if edition_membership is not None:
        table["edition-membership"] = edition_membership
    return table
