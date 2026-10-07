"""Load and parse a ``service.yaml`` into a :class:`Service`.

Uses :mod:`yaml` (PyYAML). ``safe_load`` is used on purpose: service files are
trusted local config, and ``safe_load`` refuses arbitrary object
instantiation.
"""

from __future__ import annotations

from typing import Any

import yaml

from .model import Service

# Scalar spellings (case-insensitive) that unambiguously mean a boolean. A
# ``service.yaml`` may write ``reachable: false`` (bool), ``reachable: 'false'``
# (str), or even ``reachable: 0`` (int); accept every common spelling and reject
# anything ambiguous with a clean load error.
_TRUE_STRINGS = frozenset({"true", "1", "yes", "on"})
_FALSE_STRINGS = frozenset({"false", "0", "no", "off"})


class ServiceLoadError(ValueError):
    """Raised when a ``service.yaml`` cannot be parsed into a ``Service``."""


def load_service(path: str) -> Service:
    """Parse the ``service.yaml`` at ``path`` into a :class:`Service`.

    Raises:
        ServiceLoadError: if the file is missing, not valid YAML, or is not a
            mapping.
    """

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ServiceLoadError(f"cannot read {path!r}: {exc}") from exc

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ServiceLoadError(f"{path!r} is not valid YAML: {exc}") from exc

    if data is None:
        raise ServiceLoadError(f"{path!r} is empty")
    if not isinstance(data, dict):
        raise ServiceLoadError(
            f"{path!r} must be a YAML mapping, got {type(data).__name__}"
        )

    return _from_dict(data)


def _scalar(value: Any, where: str) -> str:
    """Coerce a YAML scalar to ``str``.

    YAML parses unquoted numbers/booleans as ``int``/``float``/``bool``; a
    ``service.yaml`` like ``NGINX_PORT: 80`` is common, so we stringify scalars
    rather than rejecting them.
    """

    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    raise ServiceLoadError(
        f"{where} must be a scalar, got {type(value).__name__}"
    )


def _as_str(value: Any, where: str) -> str:
    return _scalar(value, where)


def _as_bool(value: Any, where: str) -> bool:
    """Coerce a YAML scalar to ``bool`` for a boolean field.

    Accepts an actual ``bool``, a ``1``/``0`` int, or a scalar string/int that
    unambiguously means true/false (``true``/``false``, ``1``/``0``,
    ``yes``/``no``, ``on``/``off`` — case-insensitive). Anything else (a bare
    non-0/1 int, or an ambiguous string) is a clean load error, so a typo like
    ``reachable: maybe`` fails loudly instead of being silently coerced.
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
        raise ServiceLoadError(
            f"{where} must be a boolean, got {value!r}"
        )
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _TRUE_STRINGS:
            return True
        if key in _FALSE_STRINGS:
            return False
        raise ServiceLoadError(
            f"{where} must be a boolean (true/false), got {value!r}"
        )
    raise ServiceLoadError(
        f"{where} must be a boolean, got {type(value).__name__}"
    )


def _as_str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ServiceLoadError(f"{where} must be a list, got {type(value).__name__}")
    return [_scalar(item, where) for item in value]


def _as_str_map(value: Any, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ServiceLoadError(f"{where} must be a mapping, got {type(value).__name__}")
    return {
        _scalar(key, where): _scalar(val, where) for key, val in value.items()
    }


def _from_dict(data: dict) -> Service:
    name = _as_str(data["name"], "name") if "name" in data else ""
    image = _as_str(data["image"], "image") if "image" in data else ""
    user = _as_str(data["user"], "user") if "user" in data else None
    restart = _as_str(data["restart"], "restart") if "restart" in data else "on-failure"
    # ``reachable`` defaults to True (US-SRV-2 services are reachable on the LAN);
    # set ``reachable: false`` to opt out of the reachability policy for
    # non-network services (workers/cron).
    reachable = _as_bool(data["reachable"], "reachable") if "reachable" in data else True

    return Service(
        name=name,
        image=image,
        user=user,
        restart=restart,
        ports=_as_str_list(data.get("ports"), "ports"),
        volumes=_as_str_list(data.get("volumes"), "volumes"),
        env=_as_str_map(data.get("env"), "env"),
        reachable=reachable,
    )
