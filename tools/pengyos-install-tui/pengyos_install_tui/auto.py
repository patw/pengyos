"""Parse ``install.toml`` into the same answer set the interactive path produces.

This module is the ``--auto`` front-end (spec.md: "a non-interactive
``--auto install.toml``). It reads an ``install.toml``, applies each field to the
pure :class:`~pengyos_install_tui.model.Wizard`, and returns the confirmed
:class:`~pengyos_install_tui.model.Plan` — the *exact* answer set the
interactive ``run`` path produces (spec.md: "auto(config) equals the interactive
answer set for the same inputs").

The :class:`Plan` is the single source of truth: ``auto`` does not build the
answer dict itself, it drives the wizard and lets :meth:`Plan.to_dict` render it,
so the two front-ends can never drift apart.

A missing or invalid field is a **reported error, not a crash** (spec.md):

* a file that cannot be read, a document that is not valid TOML, or a top-level
  value that is not a table raises :class:`ConfigError` (with a message);
* a field that fails a step's validator raises the model's
  :class:`~pengyos_install_tui.model.ValidationError` (carrying ``.message``);
* a required field that is absent raises :class:`ConfigError` naming it.

The module is stdlib-only and offline: the only I/O it performs is reading one
TOML file, and the block-device/size facts are injected via an optional
``probe`` (exactly as for :class:`~pengyos_install_tui.model.Wizard`), so the
whole parser is golden-testable without a file, a disk, or root.

Public surface
--------------
* :class:`ConfigError` — a reported structural/parse/missing-field error.
* :data:`STEPS` / :data:`REQUIRED` — the answerable steps ``auto`` knows, and
  the subset it requires (``pengy`` is optional).
* :func:`read_config` — read + parse a TOML file into a plain dict.
* :func:`build_plan` — apply an already-parsed dict to the wizard (pure).
* :func:`parse_config` — read a file and build the plan (the public entry point).
* :func:`plan_to_json` — serialise a :class:`Plan` to a JSON string.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import replace
from typing import Optional

from pengyos_install_tui.model import (
    Plan,
    Probe,
    ValidationError,
    Wizard,
)

__all__ = [
    "ConfigError",
    "STEPS",
    "REQUIRED",
    "read_config",
    "build_plan",
    "parse_config",
    "plan_to_json",
]


class ConfigError(Exception):
    """A reported problem with ``install.toml`` — never a traceback.

    Raised for a file that cannot be read, a document that is not valid TOML, a
    top-level value that is not a table, or a required field that is absent.
    Carries the human-readable ``message`` (and the offending ``path`` when one
    is in play) so the CLI can render a clean error rather than a crash.
    """

    def __init__(self, message: str, path: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = path


#: The answerable steps ``auto`` applies, in wizard order (excludes ``confirm``
#: — there is nothing to parse for the review step).
STEPS: tuple[str, ...] = (
    "target",
    "edition",
    "layout",
    "user",
    "network",
    "pengy",
)

#: The required fields, in display order. Every one must be present in the
#: document; a missing one is a :class:`ConfigError` naming it. ``pengy`` is
#: absent: the assistant's endpoint is always skippable, so a document without
#: it is complete and Pengy keeps the defaults the image ships.
REQUIRED: tuple[str, ...] = (
    "target",
    "edition",
    "layout",
    "user",
    "network",
)


def read_config(path: str) -> dict:
    """Read and parse an ``install.toml`` file into a plain dict.

    Raises :class:`ConfigError` (with the offending ``path``) when the file is
    missing/unreadable, the document is not valid TOML, or the top-level value
    is not a table.
    """
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"config file not found: {path}", path=path
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"cannot read config file '{path}': {exc}", path=path
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"invalid TOML in '{path}': {exc}", path=path
        ) from exc

    # tomllib always yields a table for valid TOML, but keep the guard so the
    # public contract ("a mapping of fields") holds even if a future parser
    # changes. build_plan enforces the same contract on an in-pass mapping.
    if not isinstance(data, dict):
        raise ConfigError(
            f"invalid config '{path}': top level must be a table of fields",
            path=path,
        )
    return data


def build_plan(config: dict, probe: Optional[Probe] = None) -> Plan:
    """Apply an already-parsed ``install.toml`` mapping to the wizard.

    Each of :data:`REQUIRED` must be present (a missing one raises
    :class:`ConfigError`); each value is applied through the wizard's step
    validator (an invalid one raises :class:`ValidationError`). On success the
    wizard is confirmed and the :class:`Plan` is returned — the exact answer set
    the interactive path produces.
    """
    if not isinstance(config, dict):
        raise ConfigError("config must be a table of fields")
    wizard = Wizard(probe=probe)  # type: ignore[arg-type]
    for step in STEPS:
        if step not in config:
            if step in REQUIRED:
                raise ConfigError(f"missing required field: '{step}'", path=None)
            # The optional step: an absent field is the "skipped" answer.
            wizard.answer(step, "")
            continue
        # A bad value propagates as the model's ValidationError (a message,
        # never a crash); a missing key is already handled above.
        wizard.answer(step, config[step])
    return wizard.confirm()


def parse_config(path: str, probe: Optional[Probe] = None) -> Plan:
    """Read ``install.toml`` at ``path`` and build the confirmed plan.

    The public entry point: read + parse the file, then :func:`build_plan`.
    Raises :class:`ConfigError` for structural/parse/missing-field problems and
    :class:`ValidationError` for an invalid field — both carry ``.message``.
    """
    return build_plan(read_config(path), probe=probe)  # type: ignore[arg-type]


def plan_to_json(plan: Plan) -> str:
    """Serialise a :class:`Plan` to a compact, stable JSON string.

    The machine-readable form emitted by ``auto --json``; the single source of
    truth is :meth:`Plan.to_dict`, so ``auto`` and ``run --json`` agree exactly.
    """
    return json.dumps(plan.to_dict(), indent=2, sort_keys=True)
