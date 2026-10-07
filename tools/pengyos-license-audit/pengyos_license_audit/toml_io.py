"""TOML read/write helpers.

Runtime is stdlib-only, so we use :mod:`tomllib` for reading (Python 3.11+) and
fall back to :mod:`tomli` when available. Writing is a tiny, dependency-free
serializer that only supports the flat shapes this tool emits.
"""

from __future__ import annotations

import json
import os
from typing import Any


def _load_tomllib() -> Any:
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "no TOML parser available (need Python 3.11+ or the 'tomli' package)"
            ) from exc
    return tomllib


def read_toml(path: str) -> Any:
    """Read and parse a TOML file, raising OSError if it cannot be read."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "rb") as fh:
        return _load_tomllib().load(fh)


def write_toml(path: str, data: dict[str, Any]) -> None:
    """Write *data* as TOML.

    Supports only the simple scalar / list-of-scalars / list-of-tables shapes
    this tool produces. For anything richer, raise rather than emit garbage.
    """
    try:
        import tomli_w  # type: ignore
    except ModuleNotFoundError:
        raise RuntimeError(
            "tomli_w is required to write TOML (pip install tomli_w)"
        )
    with open(path, "wb") as fh:
        tomli_w.dump(data, fh)


def write_json(path: str, data: dict[str, Any]) -> None:
    """Write *data* as pretty, stable JSON."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")


def read_json(path: str) -> Any:
    """Read and parse a JSON file."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
