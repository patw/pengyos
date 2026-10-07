"""Diff two hwprofile ``profile.toml`` files (added / removed / changed devices).

``pengyos-fleet diff`` compares two collected profiles and reports, per hardware
list section, the devices that were **added**, **removed**, or **changed**
between them. It is the counterpart to the deterministic emitter in
:mod:`pengyos_fleet.emit`: because ``collect`` writes a stable, sorted
``profile.toml`` per host, ``diff`` gives a stable, readable report of what
changed between two hosts or two points in time.

The comparison reuses the sibling ``pengyos_hwprofile`` package **when it is
importable** (see ``tests/conftest.py`` for how the sibling is made importable
in the test suite): its ``load_profile`` validates each document against the
hwprofile schema, and its diff identity/output conventions are mirrored here so
the two tools agree. When the sibling is *not* importable, this module falls
back to a documented, standard-library-only local copy — the output is
identical either way, so the tests can lock the format without the sibling on
``sys.path``.

Only the hardware *list* sections (``memory``, ``gpu``, ``storage``,
``network``, ``peripherals``) are diffed. ``metadata`` and ``cpu`` are machine
and tool identity, not hardware, so they are intentionally not compared.

Each section is keyed by its own *identity* fields (the fields that say which
physical device an item refers to). Two items with the same identity are the
same device, so a value change is reported as ``~ changed``; an item with a
unique identity is ``+ added`` (present only in the second) or ``- removed``
(present only in the first). This is what lets ``diff`` tell a changed device
apart from a replaced one. Standard library only.
"""

from __future__ import annotations

import os
import tomllib
from typing import Any, Mapping, Sequence

# The hardware list sections, in the order they appear in a profile. Matches the
# hwprofile emitter's ``_LIST_ORDER`` and the fleet emitter's ``_LIST_ORDER``.
_LIST_ORDER: tuple[str, ...] = ("memory", "gpu", "storage", "network", "peripherals")

# Each section's *identity* fields: the fields that identify a physical device.
# Two items that agree on all of these are the same device (so a value change is
# reported as ``~ changed``); an item with a unique identity is added/removed.
#
# This mirrors the sibling ``pengyos_hwprofile`` diff for every section except
# ``network``: there the interface *name* is treated as the physical identity and
# the MAC is a property (so a MAC change is a ``~ changed``, not a removal plus a
# fresh addition). A ``mac`` in the identity would conflate a config change with
# a hardware swap, which is exactly what a hardware inventory wants to avoid.
_SECTION_IDENTITY: dict[str, tuple[str, ...]] = {
    "memory": ("locator",),
    "gpu": ("bus",),
    "storage": ("serial", "bus", "model"),
    "network": ("interface",),
    "peripherals": ("bus", "vendor_id", "product_id"),
}


class DiffError(RuntimeError):
    """Raised when a profile cannot be loaded or does not conform.

    Covers a missing/unreadable file, invalid TOML, and a schema violation, so a
    bad profile is refused loudly rather than silently producing a partial diff.
    """


# ---------------------------------------------------------------------------
# Loading: reuse the hwprofile validator when importable; otherwise the local
# copy. Both raise a typed error on a schema violation.
# ---------------------------------------------------------------------------
try:  # pragma: no cover - exercised implicitly depending on the environment
    from pengyos_hwprofile.schema import (  # type: ignore
        load_profile as _hwprofile_load_profile,
        ProfileError as _HWProfileError,
    )

    _USES_HWPROFILE = True
except Exception:  # pragma: no cover - the fallback path
    _hwprofile_load_profile = None
    _HWProfileError = None
    _USES_HWPROFILE = False


# The mandatory fields of the two mandatory tables, mirroring the hwprofile
# schema's required fields for ``metadata`` and ``cpu``. Anything a real profile
# must carry is checked here so the local validator refuses a malformed profile
# even when the sibling package is absent.
_MANDATORY_FIELDS: dict[str, tuple[str, ...]] = {
    "metadata": ("hostname", "kernel", "tool_version"),
    "cpu": ("vendor", "model", "cores", "threads", "mhz"),
}


def _validate_local(data: Mapping[str, Any]) -> None:
    """Validate a parsed profile against a documented local schema copy.

    A faithful, standard-library-only subset of the hwprofile schema: the top
    level is a table with only known sections, the two mandatory tables
    (``metadata`` / ``cpu``) are present with their mandatory fields, and every
    present hardware list section is a list. Raises :class:`DiffError` on any
    violation. Kept in lock-step with ``pengyos_hwprofile.schema`` on purpose.
    """

    if not isinstance(data, Mapping):
        raise DiffError("expected a table at the top level")

    known = {"metadata", "cpu", "memory", "gpu", "storage", "network", "peripherals"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise DiffError(f"unknown top-level section(s): {', '.join(unknown)}")

    missing = sorted({"metadata", "cpu"} - set(data))
    if missing:
        raise DiffError(f"missing required section(s): {', '.join(missing)}")

    for table, fields in _MANDATORY_FIELDS.items():
        if not isinstance(data[table], Mapping):
            raise DiffError(f"{table}: expected a table")
        missing_fields = sorted(set(fields) - data[table].keys())
        if missing_fields:
            raise DiffError(
                f"{table}: missing required field(s): {', '.join(missing_fields)}"
            )

    for section in _LIST_ORDER:
        if section in data and not isinstance(data[section], list):
            raise DiffError(f"{section}: expected a list of tables")


def load_profile(path: str | os.PathLike[str]) -> Mapping[str, Any]:
    """Load and validate a single ``profile.toml`` from ``path``.

    Parses the TOML file and validates it against the hwprofile schema (reused
    from the sibling package when importable, otherwise a documented local
    copy). Raises :class:`DiffError` if the file is missing/unreadable, is not
    valid TOML, or does not conform to the schema.
    """

    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise DiffError(f"profile not found: {path}") from exc
    except OSError as exc:
        raise DiffError(f"cannot read {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise DiffError(f"{path} is not valid TOML: {exc}") from exc

    validator = _hwprofile_load_profile if _USES_HWPROFILE else _validate_local
    try:
        validator(data)
    except Exception as exc:  # noqa: BLE001 - normalise validator errors to DiffError
        # hwprofile's validator raises ``ProfileError``; the local copy raises
        # ``DiffError``. Either way we surface a single typed error, unless it is
        # not a hwprofile schema error (then it is re-raised unchanged).
        if _HWProfileError is not None and isinstance(exc, _HWProfileError):
            raise DiffError(str(exc)) from exc
        raise
    return data


# ---------------------------------------------------------------------------
# Comparison.
# ---------------------------------------------------------------------------


def _identity(item: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, ...]:
    """A stable identity for ``item`` from ``fields`` (missing -> ``""``)."""

    return tuple(str(item.get(field) or "") for field in fields)


def _describe(item: Mapping[str, Any], fields: Sequence[str]) -> str:
    """A short, human-readable label for an item from its identity ``fields``.

    Joins the non-empty identity values with ``/`` so a device is named by what
    identifies it (e.g. ``S123N0M2162001/nvme`` for storage), falling back to a
    positional index when its identity is empty.
    """

    values = [str(item.get(field) or "") for field in fields]
    present = [v for v in values if v]
    if present:
        return "/".join(present)
    return "#?"


def _changed_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Return the identity-keyed fields whose value changed between two items."""

    changed = sorted(
        field
        for field in set(before) | set(after)
        if _scalar(before.get(field)) != _scalar(after.get(field))
    )
    return changed


def _scalar(value: Any) -> str:
    """Render a scalar for comparison (bools and numbers normalised)."""

    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


def diff_documents(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> list[tuple[str, str, str]]:
    """Compute the structured diff between two profile documents.

    Returns a list of ``(kind, section, key)`` tuples in a stable order, where
    ``kind`` is ``"+"`` (added), ``"-"`` (removed) or ``"~"`` (changed). Only the
    hardware list sections are compared; ``metadata`` and ``cpu`` are ignored.
    Within a section, added items come first (sorted), then removed (sorted),
    then changed (sorted); sections follow the fixed ``_LIST_ORDER``.
    """

    results: list[tuple[str, str, str]] = []
    for section in _LIST_ORDER:
        id_fields = _SECTION_IDENTITY[section]
        before_items = before.get(section) or []
        after_items = after.get(section) or []

        by_id_before = {
            _identity(item, id_fields): item for item in before_items
        }
        by_id_after = {_identity(item, id_fields): item for item in after_items}

        added = sorted(set(by_id_after) - set(by_id_before))
        removed = sorted(set(by_id_before) - set(by_id_after))
        common = sorted(set(by_id_before) & set(by_id_after))

        for key in added:
            results.append(("+", section, _describe(by_id_after[key], id_fields)))
        for key in removed:
            results.append(("-", section, _describe(by_id_before[key], id_fields)))
        for key in common:
            if _changed_fields(by_id_before[key], by_id_after[key]):
                results.append(("~", section, _describe(by_id_before[key], id_fields)))
    return results


def render_diff(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    label_a: str = "A",
    label_b: str = "B",
) -> str:
    """Render the structured diff between two documents as report text.

    Each change is one line — ``+ {section}: {key}`` for added, ``- {section}:
    {key}`` for removed, ``~ {section}: {key}`` for changed. When the documents
    are identical the output is a single "identical" line. The text ends with a
    single trailing newline.
    """

    changes = diff_documents(before, after)
    if not changes:
        return f"{label_a} and {label_b} are identical\n"

    lines = [f"{kind} {section}: {key}" for kind, section, key in changes]
    return "\n".join(lines) + "\n"
