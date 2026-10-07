"""``layout.toml`` serialization: a small stdlib TOML emitter + round-trip.

The standard library can only *read* TOML (``tomllib``, 3.11+), so this module
emits the small TOML subset ``layout.toml`` needs, golden-tested in
``tests/test_toml_io.py``:

- top-level ``key = value`` pairs (basic strings, booleans);
- a ``[[subvolumes]]`` array of tables, in layout (creation) order.

``layout_to_toml`` turns a validated :class:`~pengyos_btrfs.layout.Layout` into
a deterministic ``layout.toml`` document. ``layout_from_toml`` parses it back
with ``tomllib`` and re-validates it through
:func:`~pengyos_btrfs.layout.validate_layout`, so a round-trip
(``layout_from_toml(layout_to_toml(layout)) == layout``) is a load-bearing
guarantee.

One deliberate encoding decision: **``mount`` is omitted** for subvolumes that
are not mounted (``mount is None``, e.g. ``@snapshots``). TOML has no null,
and "no key" is the honest representation of "not a mount point" —
``layout_from_toml`` restores ``None`` for a missing ``mount``.

Malformed input never surfaces as a traceback: bad TOML and structurally
invalid layouts raise :class:`pengyos_btrfs.errors.LayoutError` with a
precise message.
"""

from __future__ import annotations

import tomllib
from typing import Optional

from .errors import LayoutError
from .layout import Layout, Subvolume, validate_layout

__all__ = ["layout_to_toml", "layout_from_toml"]

# Characters that MUST be escaped in a TOML basic string (control chars that
# are not allowed raw): U+0000-U+0008, U+000A-U+001F, U+007F-U+009F. We also
# escape the "friendly" ones (\b \t \n \f \r) via their short escapes for
# readability, and leave everything else raw (TOML strings are UTF-8).
_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _escape_basic_string(value: str) -> str:
    """Encode *value* as a TOML basic (double-quoted) string literal."""
    out = ['"']
    for ch in value:
        if ch in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[ch])
            continue
        code = ord(ch)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _toml_value(key: str, value) -> str:
    """Encode one ``key = value`` line; ``None`` values are omitted."""
    if value is None:
        return ""
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, str):
        rendered = _escape_basic_string(value)
    else:
        raise LayoutError(
            f"cannot serialize field {key!r}: unsupported type "
            f"{type(value).__name__} (only str and bool are part of "
            f"layout.toml)"
        )
    return f"{key} = {rendered}"


def layout_to_toml(layout: Layout) -> str:
    """Serialize *layout* to a deterministic ``layout.toml`` document.

    Shape (pinned by a golden test)::

        filesystem = "pengyos"
        snapshot_dir = "@snapshots"

        [[subvolumes]]
        name = "@"
        path = "@"
        read_only = false
        mount = "/"
        description = "..."
        ...

    ``mount`` lines are emitted only for mounted subvolumes (``mount is not
    None``); the unmounted snapshot parent directory has no ``mount`` key.
    """
    if not isinstance(layout, Layout):
        raise LayoutError(
            f"layout_to_toml expects a Layout, got {type(layout).__name__}"
        )

    lines = [
        "# PengyOS btrfs subvolume layout",
        f"# filesystem: {layout.filesystem}",
        "",
        f"filesystem = {_escape_basic_string(layout.filesystem)}",
        f"snapshot_dir = {_escape_basic_string(layout.snapshot_dir)}",
    ]
    for s in layout.subvolumes:
        lines.append("")
        lines.append("[[subvolumes]]")
        lines.append(_toml_value("name", s.name))
        lines.append(_toml_value("path", s.path))
        lines.append(_toml_value("read_only", s.read_only))
        mount_line = _toml_value("mount", s.mount)
        if mount_line:
            lines.append(mount_line)
        lines.append(_toml_value("description", s.description))
    return "\n".join(lines) + "\n"


def layout_from_toml(text: str) -> Layout:
    """Parse a ``layout.toml`` document back into a validated
    :class:`Layout`.

    A missing ``mount`` key means "not mounted" and is restored to
    ``None``. The result is re-validated with
    :func:`~pengyos_btrfs.layout.validate_layout`, so a hand-edited or
    truncated document that no longer describes a valid layout raises
    :class:`LayoutError` rather than silently producing a broken plan.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise LayoutError(f"not valid TOML: {exc}") from exc

    filesystem: Optional[str] = data.get("filesystem") if isinstance(data, dict) else None
    if not isinstance(filesystem, str):
        raise LayoutError("missing or non-string 'filesystem' key")

    snapshot_dir = data.get("snapshot_dir") if isinstance(data, dict) else None
    if not isinstance(snapshot_dir, str):
        raise LayoutError("missing or non-string 'snapshot_dir' key")

    raw = data.get("subvolumes") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise LayoutError("missing or empty 'subvolumes' table array")

    subvolumes = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise LayoutError(f"subvolumes[{i}] is not a table")
        for key in ("name", "path"):
            if not isinstance(entry.get(key), str):
                raise LayoutError(
                    f"subvolumes[{i}] is missing a string {key!r} key"
                )
        read_only = entry.get("read_only", False)
        if not isinstance(read_only, bool):
            raise LayoutError(
                f"subvolumes[{i}].read_only must be a boolean, got "
                f"{read_only!r}"
            )
        mount = entry.get("mount")
        if mount is not None and not isinstance(mount, str):
            raise LayoutError(f"subvolumes[{i}].mount must be a string")
        description = entry.get("description", "")
        if not isinstance(description, str):
            raise LayoutError(f"subvolumes[{i}].description must be a string")
        subvolumes.append(
            Subvolume(
                name=entry["name"],
                path=entry["path"],
                read_only=read_only,
                mount=mount,
                description=description,
            )
        )

    return validate_layout(filesystem, subvolumes, snapshot_dir)
