"""Emit one hwprofile ``profile.toml`` per host.

``pengyos-fleet collect`` collects hardware facts for each host in the list and
writes one deterministic, diff-friendly ``profile.toml`` under
``--out/<name>/profile.toml``. This module owns that *emit* step: it turns the
plain ``dict`` / ``list`` document returned by
:func:`pengyos_fleet.facts.collect_facts` into TOML text and writes it to disk.

The fleet profiles are hwprofile documents, so the serialization reuses the
sibling ``pengyos_hwprofile`` package **when it is importable** (see
``tests/conftest.py`` for how the sibling is made importable in the test
suite). When the sibling is *not* importable, this module falls back to a
documented, standard-library-only local copy of the same emitter — the output
is byte-identical either way, so the tests can lock the format without the
sibling on ``sys.path``.

The local copy is kept in lock-step with ``pengyos_hwprofile.emitter`` on
purpose: the stdlib ships ``tomllib`` (read-only) but no writer, so the
hwprofile emitter is a hand-rolled TOML *writer* for exactly the subset the
schema needs. If that writer changes, copy the change here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from .facts import collect_facts
from .hosts import Host
from .runner import Runner

# ---------------------------------------------------------------------------
# Reuse the hwprofile emitter when importable; otherwise the local copy.
#
# ``render()`` returns byte-identical text either way, so the tests assert on
# the output rather than on which writer produced it.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised implicitly depending on the environment
    from pengyos_hwprofile.emitter import emit as _hwprofile_emit  # type: ignore

    _USES_HWPROFILE = True
except Exception:  # pragma: no cover - the fallback path
    _hwprofile_emit = None
    _USES_HWPROFILE = False


# ---------------------------------------------------------------------------
# Local copy of the hwprofile emitter.
#
# Keys emitted verbatim, in a fixed order, from the ``metadata`` and ``cpu``
# tables. Fixing the order keeps the two mandatory tables stable regardless of
# how the collectors built them.
# ---------------------------------------------------------------------------
_METADATA_ORDER: tuple[str, ...] = ("hostname", "kernel", "tool_version")
_CPU_ORDER: tuple[str, ...] = ("vendor", "model", "cores", "threads", "mhz")

# The hardware list sections, in the order they appear in the document.
_LIST_ORDER: tuple[str, ...] = ("memory", "gpu", "storage", "network", "peripherals")


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
            lines.append(f"{key} = {_toml_number(value)}" if not isinstance(value, str)
                         else f"{key} = {_toml_string(value)}")
            seen.add(key)
    for key in sorted(k for k in table if k not in seen):
        value = table[key]
        lines.append(f"{key} = {_toml_number(value)}" if not isinstance(value, str)
                     else f"{key} = {_toml_string(value)}")
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


def _local_emit(document: Mapping[str, Any]) -> str:
    """Serialise a profile ``document`` to deterministic ``profile.toml`` text.

    A faithful copy of :func:`pengyos_hwprofile.emitter.emit`: the ``metadata``
    and ``cpu`` tables are rendered in a fixed key order, the list sections
    appear in a fixed order, and the items within each list are sorted by a
    stable key. The document ends with a single trailing newline. Raises
    :class:`ValueError` if ``document`` is not a mapping.
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


def render(document: Mapping[str, Any]) -> str:
    """Serialise a collected document to ``profile.toml`` text.

    Reuses the sibling ``pengyos_hwprofile`` emitter when it is importable, and
    otherwise the documented local copy — the output is byte-identical. Raises
    :class:`ValueError` if ``document`` is not a mapping.
    """

    emitter = _hwprofile_emit if _USES_HWPROFILE else _local_emit
    return emitter(document)


def _write_profile(document: Mapping[str, Any], host: Host, out_dir: str | Path) -> Path:
    """Serialise ``document`` and write it to ``<out_dir>/<name>/profile.toml``.

    The parent directory is created if missing and any existing ``profile.toml``
    is overwritten. Returns the path written.
    """

    text = render(document)
    target = Path(out_dir) / host.name
    target.mkdir(parents=True, exist_ok=True)
    profile = target / "profile.toml"
    profile.write_text(text, encoding="utf-8")
    return profile


def emit_profile(runner: Runner, host: Host, out_dir: str | Path) -> Path:
    """Collect facts for ``host`` and write ``<out_dir>/<name>/profile.toml``.

    Writes unconditionally and returns the path written. The parent directory is
    created if missing, and any existing ``profile.toml`` for this host is
    overwritten — so a re-run reproduces byte-identical output (the writer is
    deterministic).
    """

    document = collect_facts(runner, host)
    return _write_profile(document, host, out_dir)


def emit_profile_if_changed(runner: Runner, host: Host, out_dir: str | Path) -> Path | None:
    """Collect facts for ``host`` and write only when the profile is stale.

    A fresh profile is rendered and compared byte-for-byte against the
    ``profile.toml`` already on disk. When they match the host is left
    untouched and ``None`` is returned; otherwise the profile is (re)written and
    the path returned. This is the "collect-only-for-changed" optimisation: an
    idempotent re-collect of an unchanged fleet makes no filesystem changes.
    """

    document = collect_facts(runner, host)
    text = render(document)
    profile = Path(out_dir) / host.name / "profile.toml"
    if profile.is_file() and profile.read_text(encoding="utf-8") == text:
        return None
    return _write_profile(document, host, out_dir)


def collect_all(runner: Runner, hosts: Sequence[Host], out_dir: str | Path) -> list[Path]:
    """Emit a profile for every host that has changed since the last collect.

    Hosts whose profile is unchanged since the previous collect are skipped (the
    writer is deterministic, so a re-run reproduces identical bytes). Returns
    the list of ``profile.toml`` paths actually written, in host order — an
    empty list on an all-unchanged re-collect.
    """

    written: list[Path] = []
    for host in hosts:
        path = emit_profile_if_changed(runner, host, out_dir)
        if path is not None:
            written.append(path)
    return written


def profile_written(out_dir: str | Path, host: Host) -> bool:
    """Return ``True`` when ``host`` already has a ``profile.toml`` on disk.

    Used by the idempotent re-collect (a later subtask) to skip hosts whose
    profile is already present.
    """

    return (Path(out_dir) / host.name / "profile.toml").is_file()
