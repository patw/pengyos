"""Pins manifest loading.

The OBSERVE stage watches a *pins manifest*: the pinned ``(name, version)``
pairs that make a PengyOS build reproducible. This module turns a pins TOML
file on disk into a list of :class:`~pengyos_upstream_watch.fetcher.Component`
objects — the same watched-component value object the :mod:`fetcher` module
consumes.

Manifest shape
--------------
A pins manifest is a TOML document with a ``component`` array of tables. Each
entry pins one upstream:

.. code-block:: toml

    [[component]]
    name = "busybox"
    version = "1.36.1"
    url = "https://busybox.net/"

* ``name`` — required, the component name (the key the fetcher matches).
* ``version`` — required, the currently pinned version string.
* ``url`` — optional, an explicit upstream URL the :class:`HttpFetcher` uses to
  find the latest release. Omit it for the fetcher's ecosystem defaults.

The manifest may legitimately be empty (no pins to watch); a missing or empty
``component`` list therefore yields an empty list, not an error. A malformed
manifest (wrong shape, a missing ``name``/``version``, a non-string field) is a
parsing error and raises :class:`PinsError`.

Standard library only.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .fetcher import Component

__all__ = ["PinsError", "load_pins", "PinsManifest"]


class PinsError(ValueError):
    """The pins manifest is missing or malformed.

    A subclass of :class:`ValueError` so a bad manifest is a clean *ran-but-
    failed* (exit 1) in the CLI rather than a traceback.
    """


class PinsManifest:
    """A validated pins manifest: the ordered list of pinned components.

    Attributes
    ----------
    components:
        The pinned components, in manifest order. Duplicates by ``name`` are
        collapsed keeping the last occurrence (later pins override earlier
        ones for the same name).
    """

    def __init__(self, components: list[Component]) -> None:
        self.components = components

    def __len__(self) -> int:
        return len(self.components)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"PinsManifest({self.components!r})"


def _coerce_str(value: object, field: str, where: str) -> str:
    """Return ``value`` as a non-empty ``str`` or raise :class:`PinsError`."""
    if not isinstance(value, str):
        raise PinsError(f"{where}: field {field!r} must be a string, got {value!r}")
    text = value.strip()
    if not text:
        raise PinsError(f"{where}: field {field!r} must not be empty")
    return text


def _build_component(entry: object, index: int) -> Component:
    """Build a :class:`Component` from one ``[[component]]`` table."""
    where = f"component[{index}]"
    if not isinstance(entry, dict):
        raise PinsError(f"{where}: expected a table, got {type(entry).__name__}")

    if "name" not in entry:
        raise PinsError(f"{where}: missing required field 'name'")
    if "version" not in entry:
        raise PinsError(f"{where}: missing required field 'version'")

    name = _coerce_str(entry["name"], "name", where)
    version = _coerce_str(entry["version"], "version", where)

    entry_url = entry.get("url")
    url = _coerce_str(entry_url, "url", where) if entry_url is not None else None

    return Component(name=name, version=version, url=url)


def load_pins(path: Path | str) -> PinsManifest:
    """Load and validate a pins manifest from ``path``.

    Raises :class:`PinsError` when the file is missing, unreadable, not valid
    TOML, or has a shape that does not describe pinned components.
    """
    path = Path(path)
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise PinsError(f"pins file not found: {path}") from exc
    except OSError as exc:
        raise PinsError(f"could not read pins file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PinsError(f"could not parse pins manifest {path}: {exc}") from exc

    return _pins_from_document(data, path)


def _pins_from_document(data: object, path: Path) -> PinsManifest:
    """Build a :class:`PinsManifest` from an already-parsed TOML document."""
    if not isinstance(data, dict):
        raise PinsError(
            f"pins manifest {path}: top-level document must be a table, "
            f"got {type(data).__name__}"
        )

    raw = data.get("component", [])
    if not isinstance(raw, list):
        raise PinsError(
            f"pins manifest {path}: 'component' must be an array of tables, "
            f"got {type(raw).__name__}"
        )

    components: dict[str, Component] = {}
    for index, entry in enumerate(raw):
        component = _build_component(entry, index)
        # Last occurrence wins for a duplicate name.
        components[component.name] = component

    return PinsManifest(list(components.values()))
