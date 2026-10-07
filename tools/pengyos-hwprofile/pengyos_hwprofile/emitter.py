"""Serialise a validated profile document to deterministic ``profile.toml`` text.

The standard library ships ``tomllib`` (read-only, Python 3.11+) but *not* a
TOML writer. Rather than pull in a third-party dependency for a single, tightly
shaped document, this module renders the exact subset of TOML the hwprofile
schema needs: tables, lists of tables, strings, integers, and one float.

Determinism is a first-class requirement (two runs over the same source must
produce byte-identical output), so the emitter sorts every table key and every
list item by a stable, content-derived key. See :func:`emit` for the rules.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

# Keys emitted verbatim, in a fixed order, from the ``metadata`` and ``cpu``
# tables. Fixing the order keeps the two mandatory tables stable regardless of
# how the collectors built them.
_METADATA_ORDER = ("hostname", "kernel", "tool_version")
_CPU_ORDER = ("vendor", "model", "cores", "threads", "mhz")

# The hardware list sections, in the order they appear in the document.
_LIST_ORDER = ("memory", "gpu", "storage", "network", "peripherals")


def _toml_string(value: str) -> str:
    """Render ``value`` as a TOML basic-string (``"..."``) with escapes."""

    escaped = (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
    )
    return f'"{escaped}"'


def _toml_number(value: Any) -> str:
    """Render a number: integers bare, floats with a decimal point."""

    if isinstance(value, float):
        return repr(value)
    return str(int(value))


def _sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """A stable sort key for a list item, derived from its identifying fields.

    Items are sorted by their most specific identifier first (``serial`` /
    ``bus`` / ``vendor_id`` …), then by ``model`` / ``name``, then by ``bus`` so
    that two items differing only in bus order apart. Every field is coerced to
    a string so heterogeneous keys never raise, and ``None`` sorts first.
    """

    id_fields = ("serial", "bus", "vendor_id", "product_id", "locator", "interface")
    name_fields = ("model", "name", "vendor")
    key = tuple(str(item.get(f) or "") for f in id_fields)
    key += tuple(str(item.get(f) or "") for f in name_fields)
    return key


def _render_table(table: Mapping[str, Any], key_order: Sequence[str]) -> list[str]:
    """Render a flat table as ``key = value`` lines, honouring ``key_order``.

    Unknown keys (not in ``key_order``) are appended after the ordered ones,
    sorted, so no field is dropped. Strings are quoted; numbers are bare.
    """

    lines: list[str] = []
    seen: set[str] = set()
    for key in key_order:
        if key in table:
            value = table[key]
            if isinstance(value, str):
                lines.append(f"{key} = {_toml_string(value)}")
            else:
                lines.append(f"{key} = {_toml_number(value)}")
            seen.add(key)
    for key in sorted(k for k in table if k not in seen):
        value = table[key]
        if isinstance(value, str):
            lines.append(f"{key} = {_toml_string(value)}")
        else:
            lines.append(f"{key} = {_toml_number(value)}")
    return lines


def _render_list_section(name: str, items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Render a ``[[name]]`` array-of-tables section.

    Items are sorted by :func:`_sort_key` so the section is byte-stable across
    runs and independent of collector iteration order.
    """

    if not items:
        return []
    ordered = sorted(items, key=_sort_key)
    blocks: list[str] = []
    for item in ordered:
        blocks.append(f"[[{name}]]")  # every element needs its own header
        blocks.extend(_render_table(item, ()))
        blocks.append("")  # blank line between array-of-tables entries
    return blocks


def emit(document: Mapping[str, Any]) -> str:
    """Serialise a validated profile ``document`` to ``profile.toml`` text.

    The output is deterministic: the ``metadata`` and ``cpu`` tables are rendered
    in a fixed key order, the list sections appear in a fixed order, and the
    items within each list are sorted by a stable key. The document ends with a
    single trailing newline.

    Raises :class:`ValueError` if ``document`` is not a mapping.
    """

    if not isinstance(document, Mapping):
        raise ValueError("document must be a mapping")

    out: list[str] = []
    out.append("[metadata]")
    out.extend(_render_table(document["metadata"], _METADATA_ORDER))
    out.append("")
    out.append("[cpu]")
    out.extend(_render_table(document["cpu"], _CPU_ORDER))

    for name in _LIST_ORDER:
        items = document.get(name)
        if items:
            out.append("")
            out.extend(_render_list_section(name, items))

    return "\n".join(out).rstrip("\n") + "\n"
